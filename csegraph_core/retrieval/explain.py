from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence

from csegraph_core.core.ids import file_node_id
from csegraph_core.retrieval.constants import REASON_ORDER


LEXICAL_EVIDENCE = {
    "fts5-bm25",
    "lexical-token-overlap",
    "exact-symbol-name",
    "file-path-match",
}


EXPLANATION_BY_REASON = {
    "target": "it is the requested target.",
    "direct_call": "this function is directly called by the target.",
    "caller": "this node calls the target.",
    "import_dependency": "it lives in a file imported by the target file.",
    "same_file": "it is defined in the same file as the target.",
    "parent_class": "it is part of the target's class context.",
    "small_helper": "it is a small helper where exact source is cheap and useful.",
    "test_related": "it is test-related context for the target.",
    "raw_code_fallback": "exact source was selected as a raw-code fallback.",
    "embedding_match": "it is semantically similar to the task (embedding match).",
    "lexical_match": "it matched the task text lexically.",
    "graph_neighbor": "it is near the target in the code graph.",
}


def normalize_reasons(
    *,
    node_id: str,
    target_node_id: str,
    row: Dict[str, Any],
    target_row: Dict[str, Any],
    evidence: Sequence[str],
    lineage: Sequence[str],
    outgoing: Dict[str, List[Dict[str, Any]]],
    incoming: Dict[str, List[Dict[str, Any]]],
    symbols: Dict[str, Dict[str, Any]],
    raw_nodes: Iterable[str],
) -> List[str]:
    reasons: set[str] = set()
    raw_set = set(raw_nodes)

    if node_id == target_node_id:
        reasons.add("target")
    if _edge_exists(outgoing.get(target_node_id, []), node_id, "calls"):
        reasons.add("direct_call")
    if _edge_exists(incoming.get(target_node_id, []), node_id, "calls", source=True):
        reasons.add("caller")
    if _is_import_dependency(row, target_row, outgoing, incoming):
        reasons.add("import_dependency")
    if node_id != target_node_id and row.get("file_path") == target_row.get("file_path"):
        reasons.add("same_file")
    if _is_parent_class(node_id, target_node_id, row, target_row, symbols):
        reasons.add("parent_class")
    if _is_small_helper(node_id, target_node_id, row):
        reasons.add("small_helper")
    if _is_test_related(row):
        reasons.add("test_related")
    if node_id in raw_set:
        reasons.add("raw_code_fallback")
    if any(item == "embedding_match" for item in evidence):
        reasons.add("embedding_match")
    if any(item in LEXICAL_EVIDENCE for item in evidence):
        reasons.add("lexical_match")
    if lineage or any(item.startswith("graph-") for item in evidence):
        reasons.add("graph_neighbor")

    if not reasons:
        reasons.add("graph_neighbor")

    return [reason for reason in REASON_ORDER if reason in reasons]


def build_explanation(reasons: Sequence[str]) -> str:
    clauses = [EXPLANATION_BY_REASON[reason] for reason in reasons if reason in EXPLANATION_BY_REASON]
    if not clauses:
        return "Included because it was selected by the context ranking."
    if len(clauses) == 1:
        return f"Included because {clauses[0]}"
    return "Included because " + " ".join(clauses)


def _edge_exists(
    edges: Sequence[Dict[str, Any]],
    other_node_id: str,
    relation: str,
    *,
    source: bool = False,
) -> bool:
    key = "source_id" if source else "target_id"
    return any(edge["relation"] == relation and edge[key] == other_node_id for edge in edges)


def _is_import_dependency(
    row: Dict[str, Any],
    target_row: Dict[str, Any],
    outgoing: Dict[str, List[Dict[str, Any]]],
    incoming: Dict[str, List[Dict[str, Any]]],
) -> bool:
    node_path = row.get("file_path")
    target_path = target_row.get("file_path")
    if not node_path or not target_path or node_path == target_path:
        return False
    node_file = file_node_id(str(node_path))
    target_file = file_node_id(str(target_path))
    return (
        any(edge["relation"] == "imports" and edge["target_id"] == node_file for edge in outgoing.get(target_file, []))
        or any(edge["relation"] == "imports" and edge["source_id"] == node_file for edge in incoming.get(target_file, []))
    )


def _is_parent_class(
    node_id: str,
    target_node_id: str,
    row: Dict[str, Any],
    target_row: Dict[str, Any],
    symbols: Dict[str, Dict[str, Any]],
) -> bool:
    target_parent = target_row.get("parent_symbol_id") or target_row.get("parent_id")
    node_parent = row.get("parent_symbol_id") or row.get("parent_id")
    if target_parent and node_id == target_parent:
        return True
    if node_parent and node_parent == target_node_id:
        return True
    if target_parent and node_parent and target_parent == node_parent:
        parent = symbols.get(str(target_parent), {})
        return parent.get("kind") == "class"
    return False


def _is_small_helper(node_id: str, target_node_id: str, row: Dict[str, Any]) -> bool:
    if node_id == target_node_id or row.get("kind") not in {"function", "method"}:
        return False
    start = row.get("start_line")
    end = row.get("end_line")
    if start is None or end is None:
        return False
    return max(0, int(end) - int(start) + 1) <= 12


def _is_test_related(row: Dict[str, Any]) -> bool:
    name = str(row.get("name") or "")
    path = str(row.get("file_path") or "")
    return row.get("kind") == "test" or name.startswith("test_") or "/tests/" in f"/{path}"
