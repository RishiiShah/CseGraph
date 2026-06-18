"""MCP tool catalog for the public CseGraph context-engine surface."""
from __future__ import annotations

from mcp.types import Tool


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
                    "enum": ["small", "medium", "large"],
                    "default": "medium",
                    "description": "Retrieval profile controlling index depth.",
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
                    "enum": ["small", "medium", "large"],
                    "default": "medium",
                    "description": "Retrieval profile for refresh.",
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
            "Call this FIRST. Returns a ~150-token routing card: graph summary, top-degree key "
            "entities, detected task intent, and next-tool suggestions tailored to the task. "
            "Use this before invoking heavier tools so the agent knows which one to call."
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
            "Retrieve task-specific context from a csegraph index. "
            "Combines FTS5 lexical search, graph expansion, and sufficiency scoring "
            "to return compact or detailed code context for a task. Returned path "
            "fields are repo-relative to repo_root."
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
                    "enum": ["small", "medium", "large"],
                    "description": "Retrieval profile. Default: medium.",
                },
                "include_source": {
                    "type": "string",
                    "enum": ["auto", "always", "never"],
                    "default": "auto",
                    "description": "Control source_text materialization.",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Approximate max tokens for returned context. `max_tokens` is a soft budgeting hint used during retrieval to decide how much source material to include; it does not guarantee the serialized response size.",
                },
                "explain": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include human-readable explanations for selection.",
                },
                "detail_level": {
                    "type": "string",
                    "enum": ["auto", "minimal", "standard", "full"],
                    "default": "auto",
                    "description": "Context detail level: auto returns minimal if sufficient else standard, minimal is compact routing card with top 5 nodes, standard includes selected source, full includes all explanations.",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": MIN_BYTE_CAP,
                    "description": "Hard ceiling on the serialized JSON response size. When exceeded, source_text is dropped first, then explanations, then nodes from the tail. truncated_fields reports what was dropped.",
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
