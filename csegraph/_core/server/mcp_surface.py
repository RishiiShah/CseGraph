"""MCP public surface metrics and CI guardrail budgets.

Keeps the six-tool context loop from growing via accidental schema or prompt bloat.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from mcp.types import Prompt, Tool

# Locked counts for the agent context-engine loop (see learn.md).
EXPECTED_MCP_TOOL_COUNT = 6
EXPECTED_MCP_PROMPT_COUNT = 9

# Baselines measured from list_tools/list_prompts payloads (2026-06); fail CI if exceeded.
MAX_MCP_TOOL_SCHEMA_BYTES = 8_000
MAX_MCP_PROMPT_TEXT_BYTES = 8_000

_BLOCKING_MCP_TOOLS = frozenset({"csegraph_index", "csegraph_refresh"})


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


def measure_prompt_text_bytes(
    prompts: list[Prompt],
    *,
    render_prompt: Callable[[str, dict[str, Any] | None], Any],
) -> int:
    total = 0
    for prompt in prompts:
        result = render_prompt(prompt.name, {})
        for message in result.messages:
            content = message.content
            if hasattr(content, "text"):
                total += len(content.text or "")
            else:
                total += len(str(content or ""))
    return total


def validate_mcp_surface(
    tools: list[Tool],
    prompts: list[Prompt],
    *,
    render_prompt: Callable[[str, dict[str, Any] | None], Any],
    expected_tool_names: tuple[str, ...],
    expected_prompt_names: tuple[str, ...],
) -> dict[str, int]:
    tool_names = {tool.name for tool in tools}
    prompt_names = {prompt.name for prompt in prompts}
    if len(tools) != EXPECTED_MCP_TOOL_COUNT:
        raise AssertionError(
            f"MCP tool count {len(tools)} != {EXPECTED_MCP_TOOL_COUNT}"
        )
    if tool_names != set(expected_tool_names):
        raise AssertionError(f"MCP tool names drifted: {sorted(tool_names)}")
    if len(prompts) != EXPECTED_MCP_PROMPT_COUNT:
        raise AssertionError(
            f"MCP prompt count {len(prompts)} != {EXPECTED_MCP_PROMPT_COUNT}"
        )
    if prompt_names != set(expected_prompt_names):
        raise AssertionError(f"MCP prompt names drifted: {sorted(prompt_names)}")

    tool_bytes = measure_tool_schema_bytes(tools)
    prompt_bytes = measure_prompt_text_bytes(prompts, render_prompt=render_prompt)
    if tool_bytes > MAX_MCP_TOOL_SCHEMA_BYTES:
        raise AssertionError(
            f"MCP tool schema bytes {tool_bytes} exceed budget {MAX_MCP_TOOL_SCHEMA_BYTES}"
        )
    if prompt_bytes > MAX_MCP_PROMPT_TEXT_BYTES:
        raise AssertionError(
            f"MCP prompt text bytes {prompt_bytes} exceed budget {MAX_MCP_PROMPT_TEXT_BYTES}"
        )
    return {"tool_schema_bytes": tool_bytes, "prompt_text_bytes": prompt_bytes}
