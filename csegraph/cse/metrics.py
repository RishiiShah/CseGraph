from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Set

from csegraph.languages.python.parser import code_tokenize, extract_query_entities


DEP_THRESHOLD = 0.80
ENTITY_THRESHOLD = 0.80
SEMANTIC_THRESHOLD = 0.50
SEMANTIC_THRESHOLD_RELAXED = 0.0
CONFIDENCE_THRESHOLD = 0.70


@dataclass
class SufficiencyMetrics:
    dependency_completeness: float
    entity_coverage: float
    semantic_overlap: float
    model_confidence: float


def compute_metrics(
    task: str,
    target_node_id: str,
    context_ids: Sequence[str],
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
    outgoing: Dict[str, List[Dict[str, Any]]],
) -> SufficiencyMetrics:
    context_set = set(context_ids)
    direct_calls = {
        edge["target_id"]
        for edge in outgoing.get(target_node_id, [])
        if edge["relation"] == "calls" and edge["target_id"] in symbols
    }
    dep = 1.0 if not direct_calls else len(direct_calls & context_set) / len(direct_calls)

    names = [row["name"] for row in symbols.values()]
    entities = extract_query_entities(task, names)
    context_names = {symbols[node_id]["name"] for node_id in context_set if node_id in symbols}
    ent = 1.0 if not entities else len(entities & context_names) / len(entities)

    task_tokens = set(code_tokenize(task))
    context_tokens: Set[str] = set()
    for node_id in context_set:
        if node_id not in symbols:
            continue
        row = symbols[node_id]
        context_tokens.update(
            code_tokenize(
                " ".join(
                    [
                        row["name"],
                        row["file_path"],
                        row.get("signature") or "",
                        row.get("docstring") or "",
                        summaries.get(node_id, ""),
                    ]
                )
            )
        )
    if not task_tokens or not context_tokens:
        sem = 0.0
    else:
        sem = len(task_tokens & context_tokens) / len(task_tokens | context_tokens)

    conf = min(1.0, max(0.0, 0.45 * dep + 0.35 * ent + 0.20 * sem))
    return SufficiencyMetrics(
        dependency_completeness=round(dep, 4),
        entity_coverage=round(ent, 4),
        semantic_overlap=round(sem, 4),
        model_confidence=round(conf, 4),
    )


def all_pass(metrics: SufficiencyMetrics) -> bool:
    structural_ok = (
        metrics.dependency_completeness >= DEP_THRESHOLD
        and metrics.entity_coverage >= ENTITY_THRESHOLD
    )
    sem_threshold = SEMANTIC_THRESHOLD_RELAXED if structural_ok else SEMANTIC_THRESHOLD
    return (
        structural_ok
        and metrics.semantic_overlap >= sem_threshold
        and metrics.model_confidence >= CONFIDENCE_THRESHOLD
    )


def raw_code_nodes(
    target_node_id: str,
    context_ids: Sequence[str],
    outgoing: Dict[str, List[Dict[str, Any]]],
    metrics: SufficiencyMetrics,
    budget: int,
) -> Set[str]:
    if metrics.model_confidence >= CONFIDENCE_THRESHOLD:
        return set()
    raw: List[str] = []
    for edge in outgoing.get(target_node_id, []):
        if edge["relation"] == "calls" and edge["target_id"] in context_ids:
            raw.append(edge["target_id"])
    return set(raw[:budget])
