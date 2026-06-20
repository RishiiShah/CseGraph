from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, Dict

CONTEXT_OUTPUT_SCHEMA_VERSION = "csegraph-context-v3"


def to_dict(value: Any) -> Any:
    from csegraph._core.core.models import (
        ContextNode,
        ContextRelationship,
        ContextResult,
        ImportPrelude,
        RelationshipOccurrence,
    )

    if isinstance(value, ContextResult):
        return _context_result_to_dict(value)
    if isinstance(value, ContextNode):
        return _canonical_context_node_to_dict(value)
    if isinstance(value, ImportPrelude):
        return _import_prelude_to_dict(value)
    if isinstance(value, ContextRelationship):
        return _relationship_to_dict(value, set())
    if isinstance(value, RelationshipOccurrence):
        return _relationship_occurrence_to_dict(value)
    if is_dataclass(value):
        return {field.name: to_dict(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, list):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_dict(item) for key, item in value.items()}
    return value


def _context_result_to_dict(result: Any) -> Dict[str, Any]:
    target_node = next((node for node in result.nodes if node.id == result.target), None)
    symbol_ids = {node.id for node in result.nodes}
    return {
        "command": result.command,
        "schema_version": CONTEXT_OUTPUT_SCHEMA_VERSION,
        "repo_root": result.repo_root,
        "request": {
            "task": result.query,
            "target_input": result.target_input,
            "profile": result.profile,
            "detail_level": result.detail_level,
            "returned_detail_level": result.returned_detail_level,
            "source_policy": getattr(result, "source_policy", "auto"),
            "db_path": result.db_path,
        },
        "target": {
            "id": result.target,
            "resolution": result.target_resolution,
            "kind": target_node.kind if target_node is not None else None,
            "path": target_node.path if target_node is not None else None,
            "line_range": target_node.line_range if target_node is not None else None,
            "candidates": to_dict(result.target_candidates),
        },
        "symbols": [_canonical_context_node_to_dict(node) for node in result.nodes],
        "relationships": [
            _relationship_to_dict(relationship, symbol_ids)
            for relationship in getattr(result, "relationships", [])
        ],
        "import_preludes": to_dict(getattr(result, "import_preludes", [])),
        "confidence_breakdown": to_dict(getattr(result, "confidence_breakdown", {})),
        "sufficiency": to_dict(result.sufficiency),
        "budgets": {
            "total_estimated_tokens": result.total_estimated_tokens,
            "raw_code_nodes": to_dict(result.raw_code_nodes),
        },
        "next_actions": to_dict(result.next_actions),
        "warnings": to_dict(result.warnings),
        "run_id": result.run_id,
    }


def _canonical_context_node_to_dict(node: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": node.id,
        "kind": node.kind,
        "name": node.name,
        "language": node.language,
        "path": node.path,
        "line_range": node.line_range,
        "score": node.score,
        "reason": list(node.reason),
        "reason_details": to_dict(node.reason_details),
        "summary": node.summary,
        "estimated_tokens": node.estimated_tokens,
    }
    if node.source_text is not None:
        payload["source_text"] = node.source_text
    elif getattr(node, "source_omitted_reason", None) is not None:
        payload["source_omitted_reason"] = node.source_omitted_reason
    if node.explanation is not None:
        payload["explanation"] = node.explanation
    return payload


def _relationship_to_dict(relationship: Any, symbol_ids: set[str]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "source": relationship.source,
        "target": relationship.target,
        "relation": relationship.relation,
    }
    if relationship.metadata:
        payload["metadata"] = to_dict(relationship.metadata)
    if relationship.occurrences:
        payload["occurrences"] = to_dict(relationship.occurrences)
    if relationship.confidence != 1.0:
        payload["confidence"] = relationship.confidence
    if relationship.confidence_tier != "EXTRACTED":
        payload["confidence_tier"] = relationship.confidence_tier
    if relationship.source_path and _needs_relationship_path(relationship.source, symbol_ids):
        payload["source_path"] = relationship.source_path
    if relationship.target_path and _needs_relationship_path(relationship.target, symbol_ids):
        payload["target_path"] = relationship.target_path
    return payload


def _needs_relationship_path(endpoint: str, symbol_ids: set[str]) -> bool:
    if endpoint in symbol_ids:
        return False
    if endpoint.startswith("file::"):
        return False
    return True


def _relationship_occurrence_to_dict(occurrence: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"path": occurrence.path}
    if occurrence.line_range is not None:
        payload["line_range"] = occurrence.line_range
    if occurrence.enclosing_symbol_id is not None:
        payload["enclosing_symbol_id"] = occurrence.enclosing_symbol_id
    if occurrence.name is not None:
        payload["name"] = occurrence.name
    if occurrence.kind is not None:
        payload["kind"] = occurrence.kind
    if occurrence.metadata:
        payload["metadata"] = to_dict(occurrence.metadata)
    if occurrence.snippet is not None:
        payload["snippet"] = occurrence.snippet
    return payload


def _import_prelude_to_dict(prelude: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "path": prelude.path,
        "language": prelude.language,
        "line_range": prelude.line_range,
        "source_node_ids": to_dict(prelude.source_node_ids),
        "resolved_imports": to_dict(prelude.resolved_imports),
    }
    if prelude.text:
        payload["text"] = prelude.text
    return payload
