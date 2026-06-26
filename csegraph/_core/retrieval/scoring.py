from __future__ import annotations

import re
import sqlite3
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Set, Tuple

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
    "bug",
    "build",
    "code",
    "context",
    "does",
    "error",
    "file",
    "find",
    "first",
    "fix",
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
    "pytest",
    "regression",
    "test",
    "tests",
}
_BUG_QUERY_TOKENS = {
    "bug",
    "crash",
    "debug",
    "error",
    "exception",
    "fail",
    "failed",
    "failing",
    "failure",
    "fix",
    "regression",
    "stack",
    "trace",
    "traceback",
}
_SOURCE_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?[A-Za-z0-9_./\\-]+\."
    r"(?:py|pyi|js|jsx|ts|tsx|java|kt|kts|go|rs|rb|php|cs|cpp|cc|c|h|hpp|swift))"
    r"(?::\d+)?",
    re.IGNORECASE,
)
_STACK_SYMBOL_PATTERNS = (
    re.compile(r"\bin\s+([A-Za-z_][A-Za-z0-9_.]*)"),
    re.compile(r"\bat\s+([A-Za-z_$][A-Za-z0-9_.$]*)\s*\("),
)


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
        if not _is_explicit_test_query(task, task_tokens) and _is_test_symbol(row):
            scores[node_id] *= 0.45
    _apply_bug_evidence_boosts(task, task_tokens, symbols, scores, evidence)
    return scores, evidence


def _is_test_query(task_tokens: set[str]) -> bool:
    return bool(task_tokens & _TEST_QUERY_TOKENS)


def _is_explicit_test_query(task: str, task_tokens: set[str]) -> bool:
    if _is_test_query(task_tokens):
        return True
    lowered = task.lower().replace("\\", "/")
    return bool(
        re.search(r"\btest_[a-z0-9_]+\b", lowered)
        or "/tests/" in lowered
        or lowered.startswith("tests/")
        or "/__tests__/" in lowered
    )


def _apply_bug_evidence_boosts(
    task: str,
    task_tokens: set[str],
    symbols: Dict[str, Dict[str, Any]],
    scores: Dict[str, float],
    evidence: Dict[str, List[str]],
) -> None:
    if not (task_tokens & _BUG_QUERY_TOKENS):
        return

    path_hints = {
        match.group("path").replace("\\", "/").lower().lstrip("./")
        for match in _SOURCE_PATH_RE.finditer(task)
    }
    symbol_hints = {
        match.group(1).rsplit(".", 1)[-1].lower()
        for pattern in _STACK_SYMBOL_PATTERNS
        for match in pattern.finditer(task)
    }
    for node_id, row in symbols.items():
        path = str(row.get("file_path") or row.get("path") or "").replace("\\", "/").lower()
        name = str(row.get("name") or "").rsplit(".", 1)[-1].lower()
        if path and any(path == hint or hint.endswith(f"/{path}") for hint in path_hints):
            scores[node_id] += 12.0
            evidence[node_id].append("bug-file-evidence")
        if name and name in symbol_hints:
            scores[node_id] += 8.0
            evidence[node_id].append("bug-stack-symbol")


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
        evidence[neighbor].append(f"expanded-from-{row['source']}-via-{relation}-depth{depth}")


def apply_graph_expansion_from_maps(
    anchor: str,
    radius: int,
    scores: Dict[str, float],
    evidence: Dict[str, List[str]],
    outgoing: Dict[str, List[Dict[str, Any]]],
    incoming: Dict[str, List[Dict[str, Any]]],
    symbols: Dict[str, Dict[str, Any]],
) -> None:
    """Run the graph-expansion BFS from cached edge maps."""
    seen: set[str] = set()
    queue: Deque[Tuple[str, int, str | None, str | None]] = deque([(anchor, 0, None, None)])
    while queue:
        current, depth, source, relation = queue.popleft()
        if depth > 0:
            if current in seen:
                continue
            seen.add(current)
            if current in symbols:
                assert relation is not None
                boost = RELATION_WEIGHTS.get(relation, 0.2) / depth
                scores[current] += boost
                evidence[current].append(f"graph-{relation}")
                evidence[current].append(f"expanded-from-{source}-via-{relation}-depth{depth}")

        if depth >= radius:
            continue

        next_depth = depth + 1
        adjacent = [(edge, edge["target"]) for edge in outgoing.get(current, [])] + [
            (edge, edge["source"]) for edge in incoming.get(current, [])
        ]
        for edge, neighbor in sorted(adjacent, key=lambda item: item[0].get("id") or 0):
            queue.append((neighbor, next_depth, current, edge["relation"]))
