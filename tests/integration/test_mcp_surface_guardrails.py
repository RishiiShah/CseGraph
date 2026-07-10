"""CI guardrails for the locked six-tool MCP surface."""

from csegraph._core.server.mcp_surface import (
    EXPECTED_MCP_TOOL_COUNT,
    EXPECTED_MCP_TOOL_NAMES,
    MAX_MCP_TOOL_SCHEMA_BYTES,
    is_blocking_mcp_tool,
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


def test_all_public_mcp_tools_are_classified_as_blocking():
    blocking_tool_names = {name for name in EXPECTED_MCP_TOOL_NAMES if is_blocking_mcp_tool(name)}

    assert blocking_tool_names == EXPECTED_MCP_TOOL_NAMES
    assert not is_blocking_mcp_tool("not_a_public_tool")
