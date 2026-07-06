"""CI guardrails for the locked six-tool MCP surface."""

from csegraph._core.server.mcp_surface import (
    EXPECTED_MCP_TOOL_COUNT,
    MAX_MCP_TOOL_SCHEMA_BYTES,
    validate_mcp_surface,
)
from csegraph._core.server.tools import CORE_TOOL_NAMES, TOOLS


def test_mcp_surface_counts_names_and_schema_budget():
    metrics = validate_mcp_surface(
        TOOLS,
        expected_tool_names=tuple(CORE_TOOL_NAMES),
    )

    assert len(TOOLS) == EXPECTED_MCP_TOOL_COUNT == 6
    assert metrics["tool_schema_bytes"] <= MAX_MCP_TOOL_SCHEMA_BYTES
    assert all(tool.inputSchema["additionalProperties"] is False for tool in TOOLS)
