"""CI guardrails for the locked six-tool MCP context-engine surface."""
from __future__ import annotations

from csegraph._core.server.app import (
    _CORE_MCP_PROMPT_NAMES,
    _CORE_MCP_TOOL_NAMES,
    _PROMPTS,
    _TOOLS,
    _handle_prompt,
)
from csegraph._core.server.mcp_surface import (
    EXPECTED_MCP_PROMPT_COUNT,
    EXPECTED_MCP_TOOL_COUNT,
    MAX_MCP_PROMPT_TEXT_BYTES,
    MAX_MCP_TOOL_SCHEMA_BYTES,
    validate_mcp_surface,
)


def test_mcp_surface_counts_and_byte_budgets():
    metrics = validate_mcp_surface(
        _TOOLS,
        _PROMPTS,
        render_prompt=_handle_prompt,
        expected_tool_names=_CORE_MCP_TOOL_NAMES,
        expected_prompt_names=_CORE_MCP_PROMPT_NAMES,
    )
    assert metrics["tool_schema_bytes"] <= MAX_MCP_TOOL_SCHEMA_BYTES
    assert metrics["prompt_text_bytes"] <= MAX_MCP_PROMPT_TEXT_BYTES
    assert len(_TOOLS) == EXPECTED_MCP_TOOL_COUNT
    assert len(_PROMPTS) == EXPECTED_MCP_PROMPT_COUNT
