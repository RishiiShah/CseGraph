from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, Dict


CONTEXT_OUTPUT_SCHEMA_VERSION = "csegraph-context-v2"


def to_dict(value: Any) -> Any:
    from csegraph._core.core.models import ContextNode, ContextResult

    if isinstance(value, ContextResult):
        return _context_result_to_dict(value)
    if isinstance(value, ContextNode):
        return _canonical_context_node_to_dict(value)
    if is_dataclass(value):
        return {field.name: to_dict(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, list):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_dict(item) for key, item in value.items()}
    return value


def _context_result_to_dict(result: Any) -> Dict[str, Any]:
    return {
        "command": result.command,
        "db_path": result.db_path,
        "repo_root": result.repo_root,
        "profile": result.profile,
        "schema_version": CONTEXT_OUTPUT_SCHEMA_VERSION,
        "query": result.query,
        "target": result.target,
        "detail_level": result.detail_level,
        "returned_detail_level": result.returned_detail_level,
        "total_estimated_tokens": result.total_estimated_tokens,
        "confidence_breakdown": to_dict(getattr(result, "confidence_breakdown", {})),
        "sufficiency": to_dict(result.sufficiency),
        "raw_code_nodes": to_dict(result.raw_code_nodes),
        "next_actions": to_dict(result.next_actions),
        "warnings": to_dict(result.warnings),
        "run_id": result.run_id,
        "nodes": [_canonical_context_node_to_dict(node) for node in result.nodes],
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
        "summary": node.summary,
        "estimated_tokens": node.estimated_tokens,
    }
    if node.source_text is not None:
        payload["source_text"] = node.source_text
    if node.explanation is not None:
        payload["explanation"] = node.explanation
    return payload
