from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from csegraph_core.languages.registry import registry
from csegraph_core.text.query_tokenizer import query_tokenizer
from csegraph_core.embeddings.encoder import blob_to_vector


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


def fts_lexical_scores(
    conn: sqlite3.Connection,
    project_id: int,
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
              AND nodes.project_id = ?
            ORDER BY score
            LIMIT ?
            """,
            (*_BM25_WEIGHTS, match_expr, project_id, limit),
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
    for node_id, row in symbols.items():
        content = " ".join(
            [
                row["name"],
                row["file_path"],
                row.get("signature") or "",
                row.get("docstring") or "",
                summaries.get(node_id, ""),
            ]
        )
        lang = row["language"]
        source_tokenizer = registry.tokenizer_for(lang)
        content_tokens = set(source_tokenizer.tokenize(content))
        overlap = task_tokens & content_tokens
        if overlap:
            scores[node_id] += float(len(overlap))
            evidence[node_id].append("lexical-token-overlap")
        if row["name"].lower() in task_lower:
            scores[node_id] += 3.0
            evidence[node_id].append("exact-symbol-name")
        if row["file_path"].lower() in task_lower:
            scores[node_id] += 1.5
            evidence[node_id].append("file-path-match")
        scores[node_id] += 0.01
    return scores, evidence


def embedding_scores(
    task: str,
    embeddings: Dict[str, bytes],
    limit: int = 200,
) -> Dict[str, float]:
    from csegraph_core.embeddings.encoder import encode_single, is_available

    if not is_available() or not embeddings:
        return {}
    query_vec = encode_single(task)
    scored: List[Tuple[str, float]] = []
    for node_id, blob in embeddings.items():
        vec = blob_to_vector(blob)
        sim = _cosine_similarity(query_vec, vec)
        if sim > 0.0:
            scored.append((node_id, sim))
    scored.sort(key=lambda x: x[1], reverse=True)
    return dict(scored[:limit])


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


_BFS_CTE = """
WITH RECURSIVE bfs(node_id, depth, relation, source) AS (
    SELECT ?, 0, NULL, NULL
  UNION
    SELECT
        CASE WHEN e.source_node_id = bfs.node_id THEN e.target_node_id
             ELSE e.source_node_id END,
        bfs.depth + 1,
        e.relation,
        bfs.node_id
    FROM bfs
    JOIN edges e
      ON (e.source_node_id = bfs.node_id OR e.target_node_id = bfs.node_id)
    WHERE e.project_id = ?
      AND bfs.depth < ?
)
SELECT node_id, depth, relation, source FROM bfs WHERE depth > 0
"""


def apply_graph_expansion(
    anchor: str,
    radius: int,
    scores: Dict[str, float],
    evidence: Dict[str, List[str]],
    conn: sqlite3.Connection,
    project_id: int,
    symbols: Dict[str, Dict[str, Any]],
) -> None:
    """Run a SQLite recursive-CTE BFS from anchor up to `radius` hops.

    Avoids loading every edge for the project into Python; SQLite walks the
    edge graph itself and returns only the reachable subgraph.
    """
    seen: set[str] = set()
    for row in conn.execute(_BFS_CTE, (anchor, project_id, radius)):
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
