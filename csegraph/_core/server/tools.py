"""MCP tool catalog for the public CseGraph context-engine surface."""

from __future__ import annotations

from mcp.types import Tool

from csegraph._core.config.profiles import PROFILE_CHOICES

MIN_BYTE_CAP = 256

TOOLS: list[Tool] = [
    Tool(
        name="csegraph_index",
        description=(
            "Index a repository into a csegraph SQLite graph. "
            "Parses source files and builds nodes, edges, summaries, and FTS5 lexical rows."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Absolute path to the repository root to index.",
                },
                "profile": {
                    "type": "string",
                    "enum": list(PROFILE_CHOICES),
                    "default": "auto",
                    "description": "Retrieval profile selector. `auto` resolves to small, medium, or large from repository size.",
                },
                "db": {
                    "type": "string",
                    "description": "SQLite database path. Default: <repo>/.csegraph/index.db",
                },
                "postprocess_level": {
                    "type": "string",
                    "enum": ["none", "minimal", "full"],
                    "default": "full",
                    "description": "Postprocess level: none (fastest, parse only), minimal (FTS only), full (FTS + communities).",
                },
            },
            "required": ["repo"],
        },
    ),
    Tool(
        name="csegraph_refresh",
        description=(
            "Refresh changed files in an existing csegraph index. "
            "Re-indexes modified files, removes deleted files, and keeps unchanged data."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Absolute path to the repository root containing a .csegraph index.",
                },
                "profile": {
                    "type": "string",
                    "enum": list(PROFILE_CHOICES),
                    "default": "auto",
                    "description": "Retrieval profile selector for refresh. `auto` resolves to small, medium, or large.",
                },
                "db": {
                    "type": "string",
                    "description": "SQLite database path. Default: <repo>/.csegraph/index.db",
                },
                "postprocess_level": {
                    "type": "string",
                    "enum": ["none", "minimal", "full"],
                    "default": "full",
                    "description": "Postprocess level: none (fastest), minimal (FTS only), full (FTS + communities).",
                },
            },
            "required": ["repo"],
        },
    ),
    Tool(
        name="csegraph_minimal",
        description=(
            "Optional repository health and orientation card. Use csegraph_context directly "
            "for ordinary coding tasks; use this tool only when you need index health or "
            "high-level entry points."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Absolute path to the repository root.",
                },
                "task": {
                    "type": "string",
                    "description": "Optional natural-language task. Used for keyword-based next-tool routing.",
                },
                "db": {
                    "type": "string",
                    "description": "SQLite database path. Default: <repo>/.csegraph/index.db",
                },
            },
            "required": ["repo"],
        },
    ),
    Tool(
        name="csegraph_context",
        description=(
            "Primary one-call adaptive, edit-ready code retrieval. Returns a budgeted "
            "compact slice by default, using lexical search first and graph ranking only when needed. "
            "Use response_mode=diagnostic for selection evidence or legacy-v3 for the old shape; "
            "legacy-v3 reports sufficiency.edit_ready=false and missing_context when incomplete."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Natural-language description of the coding task.",
                },
                "repo": {
                    "type": "string",
                    "description": "Absolute path to the repository root.",
                },
                "target": {
                    "type": "string",
                    "description": "Optional target symbol name, node ID, or file path.",
                },
                "profile": {
                    "type": "string",
                    "enum": list(PROFILE_CHOICES),
                    "default": "auto",
                    "description": "Legacy-v3 retrieval profile.",
                },
                "include_source": {
                    "type": "string",
                    "enum": ["auto", "always", "never"],
                    "default": "auto",
                    "description": "Source policy: auto, always, or never.",
                },
                "token_budget": {
                    "type": "integer",
                    "minimum": 256,
                    "maximum": 16384,
                    "default": 800,
                    "description": "Whole-response budget; exact with optional tokenizer, estimated otherwise.",
                },
                "encoding": {
                    "type": "string",
                    "enum": ["o200k_base", "cl100k_base"],
                    "default": "o200k_base",
                    "description": "Encoding label for token estimation.",
                },
                "response_mode": {
                    "type": "string",
                    "enum": ["compact", "diagnostic", "legacy-v3"],
                    "default": "compact",
                    "description": "Compact, diagnostic, or legacy-v3 output.",
                },
                "engine": {
                    "type": "string",
                    "enum": ["adaptive", "legacy"],
                    "default": "adaptive",
                    "description": "Adaptive or legacy retrieval.",
                },
                "cursor": {
                    "type": "string",
                    "description": "Prior cursor for deduplicated continuation.",
                },
                "max_tokens": {
                    "type": "integer",
                    "deprecated": True,
                    "description": "Legacy-v3 source budget.",
                },
                "explain": {
                    "type": "boolean",
                    "default": False,
                    "deprecated": True,
                    "description": "Legacy-v3 selection explanations.",
                },
                "detail_level": {
                    "type": "string",
                    "enum": ["auto", "minimal", "standard", "full"],
                    "default": "auto",
                    "deprecated": True,
                    "description": "Legacy-v3 detail level.",
                },
                "task_kind": {
                    "type": "string",
                    "enum": ["auto", "edit", "understand", "review", "test-impact"],
                    "default": "auto",
                    "description": "Task intent; auto infers it.",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": MIN_BYTE_CAP,
                    "description": "Hard serialized JSON byte ceiling.",
                },
                "db": {
                    "type": "string",
                    "description": "SQLite database path. Default: <repo>/.csegraph/index.db",
                },
            },
            "required": ["task", "repo"],
        },
    ),
    Tool(
        name="csegraph_graph",
        description=(
            "Inspect the graph neighborhood around a symbol or node. "
            "Returns nodes and edges within a configurable BFS depth. "
            "Default detail_level=minimal returns a summary and top-degree nodes; "
            "use standard for the full node and edge list. "
            "Pass relations=['calls','imports',...] to restrict traversal to specific edge kinds. "
            "Returned path fields are repo-relative to repo_root."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node": {
                    "type": "string",
                    "description": "Node ID, symbol name, or file path to inspect.",
                },
                "repo": {
                    "type": "string",
                    "description": "Absolute path to the repository root.",
                },
                "depth": {
                    "type": "integer",
                    "default": 1,
                    "description": "BFS neighborhood depth.",
                },
                "detail_level": {
                    "type": "string",
                    "enum": ["minimal", "standard"],
                    "default": "minimal",
                    "description": "minimal returns summary + top-degree key nodes; standard returns the full nodes and edges.",
                },
                "relations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional edge-kind filter (e.g. ['calls','imports']). Traversal follows only these relations.",
                },
                "confidence_tiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional confidence tier filter (e.g. ['EXTRACTED']). BFS only follows edges with these tiers. Default: all tiers.",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": MIN_BYTE_CAP,
                    "description": "Hard ceiling on the serialized JSON response size. Trims edges then nodes from the tail; truncated_fields reports what was dropped.",
                },
                "db": {
                    "type": "string",
                    "description": "SQLite database path. Default: <repo>/.csegraph/index.db",
                },
            },
            "required": ["node", "repo"],
        },
    ),
    Tool(
        name="csegraph_path",
        description=(
            "Find the shortest path between two nodes in the csegraph dependency graph. "
            "Default detail_level=minimal returns a name-chain summary; "
            "use standard for the full PathStep and PathEdge sequence. "
            "Returned path fields are repo-relative to repo_root."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Source node ID, symbol name, or file path.",
                },
                "target": {
                    "type": "string",
                    "description": "Target node ID, symbol name, or file path.",
                },
                "repo": {
                    "type": "string",
                    "description": "Absolute path to the repository root.",
                },
                "detail_level": {
                    "type": "string",
                    "enum": ["minimal", "standard"],
                    "default": "minimal",
                    "description": "minimal returns the name chain + length; standard returns the full PathStep nodes and PathEdge edges.",
                },
                "relations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional edge-kind filter (e.g. ['calls','imports']). Traversal follows only these relations.",
                },
                "confidence_tiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional confidence tier filter (e.g. ['EXTRACTED']). BFS only follows edges with these tiers. Default: all tiers.",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": MIN_BYTE_CAP,
                    "description": "Hard ceiling on the serialized JSON response size. Trims edges then nodes from the tail; truncated_fields reports what was dropped.",
                },
                "db": {
                    "type": "string",
                    "description": "SQLite database path. Default: <repo>/.csegraph/index.db",
                },
            },
            "required": ["source", "target", "repo"],
        },
    ),
]

CORE_MCP_TOOL_NAMES = (
    "csegraph_index",
    "csegraph_refresh",
    "csegraph_minimal",
    "csegraph_context",
    "csegraph_graph",
    "csegraph_path",
)
TOOLS = [tool for tool in TOOLS if tool.name in CORE_MCP_TOOL_NAMES]
CORE_TOOL_NAMES = list(CORE_MCP_TOOL_NAMES)
