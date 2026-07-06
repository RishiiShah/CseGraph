from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, Dict


def to_dict(value: Any) -> Any:
    from csegraph._core.core.models import (
        ContextResponse,
        GraphEdgeView,
        GraphNodeView,
        GraphResult,
        MinimalResult,
        NextToolSuggestion,
        PathEdge,
        PathResult,
        PathStep,
    )

    if isinstance(value, ContextResponse):
        payload: Dict[str, Any] = {
            "schema_version": value.schema_version,
            "status": to_dict(value.status),
            "slices": [
                {
                    "path": item.path,
                    "lines": to_dict(item.lines),
                    "symbol": item.symbol,
                    "role": item.role,
                    "code": item.code,
                }
                for item in value.slices
            ],
        }
        for key in ("next", "diagnostics"):
            item = getattr(value, key)
            if item is not None:
                payload[key] = to_dict(item)
        for key in ("candidates", "missing", "warnings"):
            item = getattr(value, key)
            if item:
                payload[key] = to_dict(item)
        return payload
    if isinstance(value, GraphResult):
        payload = {
            "schema_version": "csegraph-graph-v2",
            "target": value.target,
            "nodes": to_dict(value.nodes),
        }
        if value.edges:
            payload["edges"] = to_dict(value.edges)
        if value.depth != 1:
            payload["depth"] = value.depth
        if value.summary:
            payload["summary"] = value.summary
        if value.total_nodes:
            payload["total_nodes"] = value.total_nodes
        if value.total_edges:
            payload["total_edges"] = value.total_edges
        if value.truncated:
            payload["truncated"] = True
        return payload
    if isinstance(value, PathResult):
        payload = {
            "schema_version": "csegraph-path-v2",
            "source": value.source,
            "target": value.target,
            "found": value.found,
        }
        if value.found:
            payload["length"] = value.length
            payload["nodes"] = to_dict(value.nodes)
        if value.edges:
            payload["edges"] = to_dict(value.edges)
        if value.summary:
            payload["summary"] = value.summary
        return payload
    if isinstance(value, GraphNodeView):
        payload = {
            "id": value.id,
            "kind": value.kind,
            "name": value.name,
            "path": value.path,
        }
        if value.line_range is not None:
            payload["line_range"] = to_dict(value.line_range)
        return payload
    if isinstance(value, GraphEdgeView):
        payload = {
            "source": value.source,
            "target": value.target,
            "relation": value.relation,
        }
        if value.confidence != 1.0:
            payload["confidence"] = value.confidence
        if value.confidence_tier != "EXTRACTED":
            payload["confidence_tier"] = value.confidence_tier
        return payload
    if isinstance(value, PathStep):
        payload = {
            "node_id": value.node_id,
            "kind": value.kind,
            "name": value.name,
            "path": value.path,
        }
        if value.line_range is not None:
            payload["line_range"] = to_dict(value.line_range)
        return payload
    if isinstance(value, PathEdge):
        return {
            "source": value.source,
            "target": value.target,
            "relation": value.relation,
        }
    if isinstance(value, NextToolSuggestion):
        payload = {"tool": value.tool}
        if value.args:
            payload["arguments"] = to_dict(value.args)
        if value.reason:
            payload["reason"] = value.reason
        return payload
    if isinstance(value, MinimalResult):
        payload = {
            "summary": value.summary,
            "entities": to_dict(value.key_entities[:3]),
        }
        if value.next_tool_suggestions:
            payload["next"] = to_dict(value.next_tool_suggestions[0])
        return payload
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: to_dict(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (list, tuple)):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_dict(item) for key, item in value.items()}
    return value
