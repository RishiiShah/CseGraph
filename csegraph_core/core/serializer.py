from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, Dict


CONTEXT_OUTPUT_SCHEMA_VERSION = "csegraph-context-v1"


def to_dict(value: Any) -> Any:
    from csegraph_core.core.models import ContextNode, ContextResult

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
        "total_estimated_tokens": result.total_estimated_tokens,
        "sufficiency": to_dict(result.sufficiency),
        "raw_code_nodes": to_dict(result.raw_code_nodes),
        "run_id": result.run_id,
        "nodes": [_canonical_context_node_to_dict(node) for node in result.nodes],
    }


def _canonical_context_node_to_dict(node: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": node.id,
        "kind": node.kind,
        "language": node.language,
        "path": node.path,
        "line_range": node.line_range,
        "reason": list(node.reason),
        "summary": node.summary,
        "source_text": node.source_text,
        "estimated_tokens": node.estimated_tokens,
    }
    if node.explanation is not None:
        payload["explanation"] = node.explanation
    return payload
