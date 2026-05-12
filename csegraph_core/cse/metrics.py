from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Set

from csegraph_core.languages.registry import registry
from csegraph_core.text.entities import extract_query_entities
from csegraph_core.text.query_tokenizer import query_tokenizer


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
    target_id: str,
    context_ids: Sequence[str],
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
    outgoing: Dict[str, List[Dict[str, Any]]],
) -> SufficiencyMetrics:
    context_set = set(context_ids)
    direct_calls = {
        edge["target_id"]
        for edge in outgoing.get(target_id, [])
        if edge["relation"] == "calls" and edge["target_id"] in symbols
    }
    dep = 1.0 if not direct_calls else len(direct_calls & context_set) / len(direct_calls)

    names = [row["name"] for row in symbols.values()]
    entities = extract_query_entities(task, names)
    context_names = {symbols[node_id]["name"] for node_id in context_set if node_id in symbols}
    ent = 1.0 if not entities else len(entities & context_names) / len(entities)

    task_tokens = set(query_tokenizer.tokenize(task))
    context_tokens: Set[str] = set()
    for node_id in context_set:
        if node_id not in symbols:
            continue
        row = symbols[node_id]
        lang = row["language"]
        source_tokenizer = registry.tokenizer_for(lang)
        context_tokens.update(
            source_tokenizer.tokenize(
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


def all_pass(
    metrics: SufficiencyMetrics,
    *,
    dep_threshold: float = DEP_THRESHOLD,
    entity_threshold: float = ENTITY_THRESHOLD,
    semantic_threshold: float = SEMANTIC_THRESHOLD,
    semantic_threshold_relaxed: float = SEMANTIC_THRESHOLD_RELAXED,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> bool:
    structural_ok = (
        metrics.dependency_completeness >= dep_threshold
        and metrics.entity_coverage >= entity_threshold
    )
    sem_threshold = semantic_threshold_relaxed if structural_ok else semantic_threshold
    return (
        structural_ok
        and metrics.semantic_overlap >= sem_threshold
        and metrics.model_confidence >= confidence_threshold
    )


def raw_code_nodes(
    target_id: str,
    context_ids: Sequence[str],
    outgoing: Dict[str, List[Dict[str, Any]]],
    metrics: SufficiencyMetrics,
    budget: int,
    *,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> Set[str]:
    if metrics.model_confidence >= confidence_threshold:
        return set()
    raw: List[str] = []
    for edge in outgoing.get(target_id, []):
        if edge["relation"] == "calls" and edge["target_id"] in context_ids:
            raw.append(edge["target_id"])
    return set(raw[:budget])
