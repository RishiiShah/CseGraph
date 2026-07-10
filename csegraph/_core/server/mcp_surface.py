"""MCP public surface metrics and CI guardrail budgets.

Keeps the six-tool context loop from growing via accidental schema or prompt bloat.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.types import Tool

# Locked counts for the agent context-engine loop (see docs/architecture.md).
EXPECTED_MCP_TOOL_COUNT = 6
EXPECTED_MCP_TOOL_NAMES = frozenset(
    {
        "csegraph_index",
        "csegraph_refresh",
        "csegraph_minimal",
        "csegraph_context",
        "csegraph_graph",
        "csegraph_path",
    }
)

# Baseline measured from the list_tools payload; fail CI if exceeded.
MAX_MCP_TOOL_SCHEMA_BYTES = 5_000

_BLOCKING_MCP_TOOLS = EXPECTED_MCP_TOOL_NAMES


def is_blocking_mcp_tool(name: str) -> bool:
    return name in _BLOCKING_MCP_TOOLS


def tool_listing_payload(tools: list[Tool]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.inputSchema,
        }
        for tool in tools
    ]


def measure_tool_schema_bytes(tools: list[Tool]) -> int:
    return len(json.dumps(tool_listing_payload(tools), sort_keys=True).encode("utf-8"))


def validate_mcp_surface(
    tools: list[Tool],
    *,
    expected_tool_names: tuple[str, ...],
) -> dict[str, int]:
    tool_names = {tool.name for tool in tools}
    if len(tools) != EXPECTED_MCP_TOOL_COUNT:
        raise AssertionError(f"MCP tool count {len(tools)} != {EXPECTED_MCP_TOOL_COUNT}")
    if tool_names != EXPECTED_MCP_TOOL_NAMES or tool_names != set(expected_tool_names):
        raise AssertionError(f"MCP tool names drifted: {sorted(tool_names)}")
    tool_bytes = measure_tool_schema_bytes(tools)
    if tool_bytes > MAX_MCP_TOOL_SCHEMA_BYTES:
        raise AssertionError(
            f"MCP tool schema bytes {tool_bytes} exceed budget {MAX_MCP_TOOL_SCHEMA_BYTES}"
        )
    return {"tool_schema_bytes": tool_bytes}
