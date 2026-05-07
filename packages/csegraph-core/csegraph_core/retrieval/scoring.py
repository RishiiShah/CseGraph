from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Dict, List, Tuple

from csegraph.languages.python.parser import code_tokenize


def lexical_scores(
    task: str,
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
) -> Tuple[Dict[str, float], Dict[str, List[str]]]:
    task_tokens = set(code_tokenize(task))
    scores: Dict[str, float] = defaultdict(float)
    evidence: Dict[str, List[str]] = defaultdict(list)
    task_lower = task.lower()
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
        content_tokens = set(code_tokenize(content))
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


def apply_graph_expansion(
    anchor: str,
    radius: int,
    scores: Dict[str, float],
    evidence: Dict[str, List[str]],
    outgoing: Dict[str, List[Dict[str, Any]]],
    incoming: Dict[str, List[Dict[str, Any]]],
    symbols: Dict[str, Dict[str, Any]],
) -> None:
    relation_weight = {"calls": 2.5, "imports": 0.8, "contains": 0.4}
    queue: deque[Tuple[str, int]] = deque([(anchor, 0)])
    visited = {anchor}
    while queue:
        current, depth = queue.popleft()
        if depth >= radius:
            continue
        for edge in outgoing.get(current, []) + incoming.get(current, []):
            neighbor = edge["target_id"] if edge["source_id"] == current else edge["source_id"]
            if neighbor not in symbols:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, depth + 1))
                continue
            boost = relation_weight.get(edge["relation"], 0.2) / (depth + 1)
            scores[neighbor] += boost
            evidence[neighbor].append(f"graph-{edge['relation']}")
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, depth + 1))
