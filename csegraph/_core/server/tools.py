"""Public MCP tool catalog for the lean CseGraph runtime."""

from __future__ import annotations

from mcp.types import Tool

_REPO = {
    "type": "string",
    "description": "Absolute path to the repository root.",
}

TOOLS: list[Tool] = [
    Tool(
        name="csegraph_index",
        description="Build the repository index, including required lexical and resolution data.",
        inputSchema={
            "type": "object",
            "properties": {"repo": _REPO},
            "required": ["repo"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="csegraph_refresh",
        description="Atomically refresh changed files in an existing repository index.",
        inputSchema={
            "type": "object",
            "properties": {"repo": _REPO},
            "required": ["repo"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="csegraph_minimal",
        description=(
            "Optional index-health and repository-orientation summary. "
            "Call csegraph_context directly for ordinary tasks."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": _REPO,
                "task": {
                    "type": "string",
                    "description": "Optional task used to choose one next action.",
                },
            },
            "required": ["repo"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="csegraph_context",
        description=(
            "Primary adaptive retrieval. Returns the compact csegraph-context-v5 contract."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Coding task to retrieve context for."},
                "repo": _REPO,
                "target": {
                    "type": "string",
                    "description": "Optional symbol, node ID, or repository-relative path.",
                },
                "task_kind": {
                    "type": "string",
                    "enum": ["auto", "edit", "understand", "review", "test-impact"],
                    "default": "auto",
                },
                "token_budget": {
                    "type": "integer",
                    "minimum": 256,
                    "maximum": 16384,
                    "default": 800,
                    "description": "Hard whole-response token budget.",
                },
                "source_mode": {
                    "type": "string",
                    "enum": ["auto", "always", "never"],
                    "default": "auto",
                },
                "diagnostic": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include retrieval diagnostics within the token budget.",
                },
            },
            "required": ["task", "repo"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="csegraph_graph",
        description="Inspect a focused graph neighborhood when context recommends expansion.",
        inputSchema={
            "type": "object",
            "properties": {
                "node": {"type": "string"},
                "repo": _REPO,
                "depth": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
                "relations": {"type": "array", "items": {"type": "string"}},
                "confidence_tiers": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["node", "repo"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="csegraph_path",
        description="Find a focused shortest dependency path between two graph nodes.",
        inputSchema={
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "target": {"type": "string"},
                "repo": _REPO,
                "relations": {"type": "array", "items": {"type": "string"}},
                "confidence_tiers": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["source", "target", "repo"],
            "additionalProperties": False,
        },
    ),
]

CORE_TOOL_NAMES = [tool.name for tool in TOOLS]
CORE_MCP_TOOL_NAMES = frozenset(CORE_TOOL_NAMES)
