from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any, Dict, List, Set, Tuple

from csegraph._core.languages.registry import registry
from csegraph._core.text.query_tokenizer import query_tokenizer
from csegraph._core.text.tokens import tokenize_node_content


RELATION_WEIGHTS: Dict[str, float] = {
    "calls": 2.5,
    "inherits": 1.5,
    "tested_by": 1.0,
    "imports": 0.8,
    "decorates": 0.6,
    "contains": 0.4,
}


_BM25_WEIGHTS = (8.0, 4.0, 2.0, 1.0, 2.0, 1.0)
# columns: name, path, signature, docstring, summary, source

_SUBSTRING_STOPWORDS = {
    "about",
    "build",
    "code",
    "does",
    "file",
    "find",
    "first",
    "from",
    "how",
    "into",
    "show",
    "task",
    "what",
    "when",
    "where",
    "which",
    "with",
}
_TEST_QUERY_TOKENS = {
    "assert",
    "coverage",
    "fail",
    "failed",
    "failing",
    "failure",
    "pytest",
    "regression",
    "test",
    "tests",
}


def fts_lexical_scores(
    conn: sqlite3.Connection,
    task: str,
    limit: int = 200,
) -> Dict[str, float]:
    """FTS5 BM25 lookup with per-column weights.

    Symbol-name matches outrank docstring matches (8x vs 1x). Returns a
    {node_id: positive_score} mapping where higher = better.
    """
    tokens = [t for t in query_tokenizer.tokenize(task) if t]
    if not tokens:
        return {}
    match_expr = " OR ".join(f'"{t}"' for t in tokens)
    try:
        rows = conn.execute(
            """
            SELECT lexical_index.node_id AS node_id,
                   bm25(lexical_index, ?, ?, ?, ?, ?, ?) AS score
            FROM lexical_index
            JOIN nodes ON nodes.id = lexical_index.node_id
            WHERE lexical_index MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (*_BM25_WEIGHTS, match_expr, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    if not rows:
        return {}
    raw = {row["node_id"]: float(row["score"]) for row in rows}
    worst = max(raw.values()) if raw else 1.0
    return {node_id: max(0.0, worst - bm25) for node_id, bm25 in raw.items()}


def lexical_scores(
    task: str,
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
    fts_seed: Dict[str, float] | None = None,
) -> Tuple[Dict[str, float], Dict[str, List[str]]]:
    task_tokens = set(query_tokenizer.tokenize(task))
    scores: Dict[str, float] = defaultdict(float)
    evidence: Dict[str, List[str]] = defaultdict(list)
    task_lower = task.lower()
    if fts_seed:
        for node_id, score in fts_seed.items():
            if node_id in symbols:
                scores[node_id] += score
                evidence[node_id].append("fts5-bm25")

    candidates: Set[str] = set()
    if fts_seed:
        candidates.update(fts_seed.keys())

    task_tokens_lower = [
        tok.lower()
        for tok in task_tokens
        if tok and len(tok) > 2 and tok.lower() not in _SUBSTRING_STOPWORDS
    ]
    if task_tokens_lower:
        for node_id, row in symbols.items():
            name_lower = row["name"].lower()
            file_lower = row["file_path"].lower()
            sig_lower = (row.get("signature") or "").lower()
            doc_lower = (row.get("docstring") or "").lower()

            for tok in task_tokens_lower:
                if (
                    tok in name_lower
                    or tok in file_lower
                    or tok in sig_lower
                    or tok in doc_lower
                    or tok in summaries.get(node_id, "").lower()
                ):
                    candidates.add(node_id)
                    break

    for node_id in candidates:
        candidate_row = symbols.get(node_id)
        if not candidate_row:
            continue
        content_tokens = tokenize_node_content(node_id, candidate_row, summaries, registry)
        overlap = task_tokens & content_tokens
        if overlap:
            scores[node_id] += float(len(overlap))
            evidence[node_id].append("lexical-token-overlap")

    for node_id, row in symbols.items():
        if row["name"].lower() in task_lower:
            scores[node_id] += 3.0
            evidence[node_id].append("exact-symbol-name")
        if row["file_path"].lower() in task_lower:
            scores[node_id] += 1.5
            evidence[node_id].append("file-path-match")
        scores[node_id] += 0.01
        if not _is_test_query(task_tokens) and _is_test_symbol(row):
            scores[node_id] *= 0.45
    return scores, evidence


def _is_test_query(task_tokens: set[str]) -> bool:
    return bool(task_tokens & _TEST_QUERY_TOKENS)


def _is_test_symbol(row: Dict[str, Any]) -> bool:
    kind = str(row.get("kind") or row.get("type") or "")
    name = str(row.get("name") or "").lower()
    path = str(row.get("file_path") or row.get("path") or "").lower()
    return (
        kind == "test"
        or name.startswith("test_")
        or "/test_" in path
        or path.startswith("test_")
        or path.startswith("tests/")
        or "/tests/" in path
        or "/__tests__/" in path
    )


_BFS_CTE = """
WITH RECURSIVE bfs(node_id, depth, relation, source) AS (
    SELECT ?, 0, NULL, NULL
  UNION
    SELECT
        CASE WHEN e.source = bfs.node_id THEN e.target
             ELSE e.source END,
        bfs.depth + 1,
        e.relation,
        bfs.node_id
    FROM bfs
    JOIN edges e
      ON (e.source = bfs.node_id OR e.target = bfs.node_id)
    WHERE bfs.depth < ?
)
SELECT node_id, depth, relation, source FROM bfs WHERE depth > 0
"""


def apply_graph_expansion(
    anchor: str,
    radius: int,
    scores: Dict[str, float],
    evidence: Dict[str, List[str]],
    conn: sqlite3.Connection,
    symbols: Dict[str, Dict[str, Any]],
) -> None:
    """Run a SQLite recursive-CTE BFS from anchor up to `radius` hops.

    Avoids loading every edge for the project into Python; SQLite walks the
    edge graph itself and returns only the reachable subgraph.
    """
    seen: set[str] = set()
    for row in conn.execute(_BFS_CTE, (anchor, radius)):
        neighbor = row["node_id"]
        if neighbor in seen:
            continue
        seen.add(neighbor)
        if neighbor not in symbols:
            continue
        relation = row["relation"]
        depth = int(row["depth"])
        boost = RELATION_WEIGHTS.get(relation, 0.2) / depth
        scores[neighbor] += boost
        evidence[neighbor].append(f"graph-{relation}")
        evidence[neighbor].append(
            f"expanded-from-{row['source']}-via-{relation}-depth{depth}"
        )
