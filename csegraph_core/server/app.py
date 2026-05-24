from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import GetPromptResult, Prompt, PromptArgument, PromptMessage, TextContent, Tool

from csegraph_core.core.models import to_dict
from csegraph_core.config.profiles import load_profile
from csegraph_core.server.session import _SESSION

logger = logging.getLogger("csegraph.mcp")

_TOOLS: list[Tool] = [
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
            "to return compact or detailed code context for a task."
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
            "Pass relations=['calls','imports',...] to restrict traversal to specific edge kinds."
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
                "max_bytes": {
                    "type": "integer",
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
            "use standard for the full PathStep and PathEdge sequence."
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
                "max_bytes": {
                    "type": "integer",
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
    Tool(
        name="csegraph_detect_changes",
        description=(
            "Detect changed symbols between the current state and a base git ref, "
            "then score each by review risk (caller count, cross-community edges, "
            "test coverage). Returns prioritized high/medium/low lists so the agent "
            "knows where to focus review effort."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Absolute path to the repository root.",
                },
                "base_ref": {
                    "type": "string",
                    "default": "HEAD~1",
                    "description": "Git ref to diff against (branch, tag, or commit). Default: HEAD~1.",
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
        name="csegraph_test_gaps",
        description=(
            "Analyze test coverage gaps in the codebase. Returns untested symbols "
            "ranked by hotspot score (caller count, cross-community edges), "
            "per-community coverage percentages, and overall coverage stats."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Absolute path to the repository root.",
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "description": "Maximum number of untested hotspots to return. Default: 20.",
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
        name="csegraph_review_questions",
        description=(
            "Generate targeted review questions from change detection and graph "
            "structure. Runs change detection internally and produces priority-ranked "
            "questions about test gaps, cross-community blast radius, and caller breakage."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Absolute path to the repository root.",
                },
                "base_ref": {
                    "type": "string",
                    "default": "HEAD~1",
                    "description": "Git ref to diff against. Default: HEAD~1.",
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
        name="csegraph_review_eval",
        description=(
            "Evaluate review intelligence against ground-truth known-risky symbols. "
            "Measures precision, recall, and F1 of risk scoring, and checks whether "
            "generated review questions address the known issues."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Absolute path to the repository root.",
                },
                "ground_truth_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of node IDs known to be risky (ground truth).",
                },
                "base_ref": {
                    "type": "string",
                    "default": "HEAD~1",
                    "description": "Git ref to diff against. Default: HEAD~1.",
                },
                "risk_threshold": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "default": "medium",
                    "description": "Risk level threshold for detection. Default: medium.",
                },
                "db": {
                    "type": "string",
                    "description": "SQLite database path. Default: <repo>/.csegraph/index.db",
                },
            },
            "required": ["repo", "ground_truth_ids"],
        },
    ),
    Tool(
        name="csegraph_vulnerabilities",
        description=(
            "Scan the codebase for security vulnerabilities using the dependency graph. "
            "Detects dangerous API calls (eval, exec, shell injection), untested security-sensitive "
            "code, hardcoded secret patterns, weak crypto, deserialization risks, and high-exposure "
            "symbols calling dangerous APIs. Returns severity-ranked results (critical/high/medium/low/info)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Absolute path to the repository root.",
                },
                "limit": {
                    "type": "integer",
                    "default": 50,
                    "description": "Maximum vulnerabilities per severity level. Default: 50.",
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
        name="csegraph_architecture",
        description=(
            "Generate community summaries and an architecture overview. "
            "Returns auto-labeled communities with key symbols, language breakdown, "
            "internal/cross-community edge counts, and coupling warnings between modules."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Absolute path to the repository root.",
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "description": "Maximum number of community summaries to return. Default: 20.",
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
        name="csegraph_flows",
        description=(
            "Trace execution flows from entry points through the call graph. "
            "Auto-detects entry points (functions with no callers, conventional names like main/handler/serve) "
            "and traces forward through CALLS edges with BFS. Returns flows ranked by criticality "
            "(file spread, depth, cross-community, test coverage gaps, security sensitivity). "
            "Use entry_point to trace from a specific function."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Absolute path to the repository root.",
                },
                "entry_point": {
                    "type": "string",
                    "description": "Optional: trace from a specific function/symbol instead of auto-detecting entry points.",
                },
                "max_depth": {
                    "type": "integer",
                    "default": 10,
                    "description": "Maximum BFS depth for flow tracing. Default: 10.",
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "description": "Maximum number of flows to return. Default: 20.",
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
        name="csegraph_resolvers",
        description=(
            "Run resolver passes to add inferred edges to the graph: transitive test coverage "
            "(BFS from test functions through call chains), Python import resolution (retry unresolved "
            "imports via __init__.py and suffix matching), and TypeScript alias resolution (tsconfig.json paths). "
            "Idempotent — safe to run multiple times."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Absolute path to the repository root.",
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
        name="csegraph_export",
        description=(
            "Export the csegraph index to GraphML, Obsidian vault, or portable JSON. "
            "GraphML can be opened in Neo4j, Gephi, or yEd. Obsidian creates a vault of "
            "linked markdown notes. JSON produces a portable graph dump."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Absolute path to the repository root.",
                },
                "output": {
                    "type": "string",
                    "description": "Output path (file for graphml/json, directory for obsidian). Default: beside the index DB.",
                },
                "format": {
                    "type": "string",
                    "enum": ["graphml", "obsidian", "json"],
                    "default": "graphml",
                    "description": "Export format. Default: graphml.",
                },
                "db": {
                    "type": "string",
                    "description": "SQLite database path. Default: <repo>/.csegraph/index.db",
                },
            },
            "required": ["repo"],
        },
    ),
]

_PROMPTS: list[Prompt] = [
    Prompt(
        name="csegraph-index",
        title="Index Repository",
        description="Build or rebuild the csegraph index for a repository.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(name="profile", description="small, medium, or large.", required=False),
        ],
    ),
    Prompt(
        name="csegraph-refresh",
        title="Refresh Repository",
        description="Refresh changed and deleted files in an existing csegraph index.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(name="profile", description="small, medium, or large.", required=False),
        ],
    ),
    Prompt(
        name="csegraph-minimal",
        title="Routing Card (Call First)",
        description="Run csegraph_minimal first to get a compact summary and next-tool suggestions.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(name="task", description="Optional task description for keyword routing.", required=False),
        ],
    ),
    Prompt(
        name="csegraph-context",
        title="Retrieve Context",
        description="Retrieve compact graph-backed context for a task and optional target.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(name="task", description="Natural-language coding task.", required=True),
            PromptArgument(name="target", description="Optional symbol, node ID, or file path.", required=False),
        ],
    ),
    Prompt(
        name="csegraph-detect-changes",
        title="Detect Changes and Score Risk",
        description="Diff against a base ref, map changed lines to graph symbols, and score review risk.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(name="base_ref", description="Git ref to diff against (default: HEAD~1).", required=False),
        ],
    ),
    Prompt(
        name="csegraph-test-gaps",
        title="Test Coverage Gaps",
        description="Identify untested symbols and coverage hotspots in the codebase.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(name="limit", description="Max hotspots to return (default: 20).", required=False),
        ],
    ),
    Prompt(
        name="csegraph-review-questions",
        title="Generate Review Questions",
        description="Generate targeted review questions from change detection and graph structure.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(name="base_ref", description="Git ref to diff against (default: HEAD~1).", required=False),
        ],
    ),
    Prompt(
        name="csegraph-review-eval",
        title="Evaluate Review Intelligence",
        description="Measure precision and recall of review intelligence against known-risky symbols.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(name="ground_truth_ids", description="Comma-separated node IDs known to be risky.", required=True),
            PromptArgument(name="base_ref", description="Git ref to diff against (default: HEAD~1).", required=False),
        ],
    ),
    Prompt(
        name="csegraph-review",
        title="Review Current Changes",
        description="Review changes with csegraph context, graph inspection, and structural report data.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(name="task", description="Optional review focus.", required=False),
        ],
    ),
    Prompt(
        name="csegraph-vulnerabilities",
        title="Security Vulnerability Scan",
        description="Scan the codebase for security vulnerabilities using the dependency graph.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(name="limit", description="Max vulnerabilities per severity (default: 50).", required=False),
        ],
    ),
    Prompt(
        name="csegraph-flows",
        title="Trace Execution Flows",
        description="Trace execution flows from entry points through the call graph, ranked by criticality.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(name="entry_point", description="Optional specific entry point to trace from.", required=False),
        ],
    ),
    Prompt(
        name="csegraph-resolvers",
        title="Run Resolver Passes",
        description="Run framework resolver passes to add inferred edges (transitive tests, imports, TS aliases).",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
        ],
    ),
    Prompt(
        name="csegraph-export",
        title="Export Graph",
        description="Export the csegraph index to GraphML, Obsidian vault, or portable JSON.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(name="format", description="graphml, obsidian, or json (default: graphml).", required=False),
            PromptArgument(name="output", description="Output file or directory path.", required=False),
        ],
    ),
    Prompt(
        name="csegraph-architecture",
        title="Architecture Overview",
        description="Generate community summaries and an architecture overview with coupling analysis.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(name="limit", description="Max communities to summarize (default: 20).", required=False),
        ],
    ),
    Prompt(
        name="csegraph-pre-merge",
        title="Pre-Merge Check",
        description="Run a pre-merge workflow using csegraph context and structural checks.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(name="task", description="Optional merge or PR description.", required=False),
        ],
    ),
]


# Prefixed to every prompt to enforce token-efficiency and escalation rules.
_TOKEN_EFFICIENCY_PREAMBLE = (
    "Token-efficiency: Prefer fewer tool calls and smaller payloads. "
    "Never make more than 3 tool calls in a single agent turn. "
    "If a minimal routing card is sufficient, prefer it to additional heavy calls."
)

def _assert_safe_path(path: Path, repo_path: Path, name: str) -> None:
    import tempfile
    resolved_path = path.resolve()
    resolved_repo = repo_path.resolve()
    if resolved_path.is_relative_to(resolved_repo):
        return
    temp_dir = Path(tempfile.gettempdir()).resolve()
    if resolved_path.is_relative_to(temp_dir):
        return
    try:
        home_dir = Path.home().resolve()
        if resolved_path.is_relative_to(home_dir):
            return
    except Exception:
        pass
    try:
        cwd_dir = Path.cwd().resolve()
        if resolved_path.is_relative_to(cwd_dir):
            return
    except Exception:
        pass
    raise ValueError(f"{name} path '{path}' must be within repository root, home directory, temporary directory, or CWD.")


def _db_path(repo: str, db: str | None = None) -> str:
    repo_path = Path(repo).resolve()
    if db:
        db_path = Path(db).resolve()
        _assert_safe_path(db_path, repo_path, "Database")
        return str(db_path)
    return str(repo_path / ".csegraph" / "index.db")


def _handle_tool(name: str, arguments: dict[str, Any]) -> Any:
    result = _dispatch_tool(name, arguments)
    _SESSION.record(name)
    # When the minimal tool runs, cache the detected task intent on the session
    # so downstream calls can route without re-detecting.
    if name == "csegraph_minimal" and isinstance(result, dict):
        intent = result.get("task_intent")
        if intent:
            _SESSION.inferred_intent = intent
    if isinstance(result, dict):
        _apply_session_filter(result)
        provided_max = arguments.get("max_bytes")
        if isinstance(provided_max, int) and provided_max > 0:
            effective_max = provided_max
        else:
            profile_name = arguments.get("profile") or "medium"
            try:
                profile_cfg = load_profile(profile_name)
                effective_max = getattr(profile_cfg, "max_bytes", None)
            except Exception:
                effective_max = None
        _apply_byte_cap(result, effective_max if isinstance(effective_max, int) and effective_max > 0 else None)
    return result


def _apply_session_filter(result: dict[str, Any]) -> None:
    """Drop next-tool suggestions whose tool has already been called this session
    and annotate the response with the current tools_already_called list.
    Mutates `result` in place."""
    called = _SESSION.tools_called
    for key in ("next_tool_suggestions", "next_actions"):
        items = result.get(key)
        if not isinstance(items, list):
            continue
        result[key] = [
            item for item in items
            if not (isinstance(item, dict) and item.get("tool") in called)
        ]
    result["tools_already_called"] = _SESSION.snapshot()


def _encoded_size(result: dict[str, Any]) -> int:
    return len(json.dumps(result, default=str).encode("utf-8"))


def _apply_byte_cap(result: dict[str, Any], max_bytes: int | None) -> None:
    """Enforce a hard ceiling on the serialized response size.

    Drop order (each step re-measures; stops when under budget):
      1. `source_text` on every node
      2. `explanation` on every node
      3. Trim `nodes` list from the tail (assumes ordering = priority)
      4. Trim `edges` list from the tail

    Annotates the response with `response_bytes`, `byte_cap`, `byte_cap_applied`,
    and `truncated_fields` so the agent knows what was dropped. Mutates `result`
    in place.

    Annotation fields are added BEFORE measurement so every size check reflects
    the final response shape. `response_bytes` is the placeholder initially and
    is overwritten with the true final size at the end.
    """
    truncated: list[str] = []
    result["truncated_fields"] = truncated
    result["byte_cap_applied"] = False
    if isinstance(max_bytes, int) and max_bytes > 0:
        result["byte_cap"] = max_bytes
    # Placeholder; we set the final value at the end. Use a value with similar
    # digit count to the cap so the size measurement stays stable.
    result["response_bytes"] = max_bytes if (isinstance(max_bytes, int) and max_bytes > 0) else 0

    if not isinstance(max_bytes, int) or max_bytes <= 0:
        _finalize_response_bytes(result)
        return

    if _encoded_size(result) <= max_bytes:
        _finalize_response_bytes(result)
        return

    nodes = result.get("nodes")

    # Step 1: drop source_text from every node.
    if isinstance(nodes, list):
        dropped = False
        for node in nodes:
            if isinstance(node, dict) and node.get("source_text") is not None:
                node.pop("source_text", None)
                dropped = True
        if dropped:
            truncated.append("source_text")
            if _encoded_size(result) <= max_bytes:
                result["byte_cap_applied"] = True
                _finalize_response_bytes(result)
                return

    # Step 2: drop explanation from every node.
    if isinstance(nodes, list):
        dropped = False
        for node in nodes:
            if isinstance(node, dict) and node.get("explanation") is not None:
                node.pop("explanation", None)
                dropped = True
        if dropped:
            truncated.append("explanation")
            if _encoded_size(result) <= max_bytes:
                result["byte_cap_applied"] = True
                _finalize_response_bytes(result)
                return

    # Step 3: trim nodes list (lowest-priority assumed at tail).
    if isinstance(nodes, list) and nodes:
        trimmed = False
        while len(nodes) > 1 and _encoded_size(result) > max_bytes:
            nodes.pop()
            trimmed = True
        if trimmed:
            truncated.append("nodes")
            if _encoded_size(result) <= max_bytes:
                result["byte_cap_applied"] = True
                _finalize_response_bytes(result)
                return

    # Step 4: trim edges list.
    edges = result.get("edges")
    if isinstance(edges, list) and edges:
        trimmed = False
        while edges and _encoded_size(result) > max_bytes:
            edges.pop()
            trimmed = True
        if trimmed:
            truncated.append("edges")

    result["byte_cap_applied"] = bool(truncated)
    _finalize_response_bytes(result)


def _finalize_response_bytes(result: dict[str, Any]) -> None:
    """Converge `response_bytes` to the actual encoded size.

    Setting `response_bytes` may shift the encoded length by a few bytes when
    the value's digit count differs from the placeholder. A short fixed-point
    loop converges in 1-2 iterations on every realistic payload.
    """
    for _ in range(4):
        new_size = _encoded_size(result)
        if result.get("response_bytes") == new_size:
            return
        result["response_bytes"] = new_size


def _dispatch_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == "csegraph_index":
        from csegraph_core.index.services import IndexService
        from csegraph_core.postprocess import PostprocessService

        repo = arguments["repo"]
        profile = arguments.get("profile", "medium")
        db = _db_path(repo, arguments.get("db"))
        result = IndexService(db).index(repo, profile=profile)
        pp_level = arguments.get("postprocess_level", "full")
        if pp_level != "none":
            PostprocessService(db).postprocess(level=pp_level)
        return to_dict(result)

    if name == "csegraph_refresh":
        from csegraph_core.index.services import RefreshService
        from csegraph_core.postprocess import PostprocessService

        repo = arguments["repo"]
        profile = arguments.get("profile", "medium")
        db = _db_path(repo, arguments.get("db"))
        result = RefreshService(db).refresh(profile=profile)
        pp_level = arguments.get("postprocess_level", "full")
        if pp_level != "none" and result.files_indexed > 0:
            PostprocessService(db).postprocess(level=pp_level)
        return to_dict(result)

    if name == "csegraph_minimal":
        from csegraph_core.retrieval.minimal import MinimalService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        return to_dict(
            MinimalService(db).first(
                task=arguments.get("task"),
                inferred_intent=_SESSION.inferred_intent,
            )
        )

    if name == "csegraph_context":
        from csegraph_core.retrieval.context import ContextService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        return to_dict(
            ContextService(db).build_context(
                task=arguments["task"],
                target=arguments.get("target"),
                profile=arguments.get("profile"),
                include_source=arguments.get("include_source", "auto"),
                max_tokens=arguments.get("max_tokens"),
                explain=arguments.get("explain", False),
                detail_level=arguments.get("detail_level", "auto"),
            )
        )

    if name == "csegraph_graph":
        from csegraph_core.graph.queries import GraphQueryService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        depth = arguments.get("depth", 1)
        detail_level = arguments.get("detail_level", "minimal")
        relations = arguments.get("relations")
        return to_dict(
            GraphQueryService(db).neighborhood(
                arguments["node"],
                depth=depth,
                detail_level=detail_level,
                relations=relations,
            )
        )

    if name == "csegraph_path":
        from csegraph_core.graph.queries import GraphQueryService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        detail_level = arguments.get("detail_level", "minimal")
        relations = arguments.get("relations")
        return to_dict(
            GraphQueryService(db).shortest_path(
                arguments["source"],
                arguments["target"],
                detail_level=detail_level,
                relations=relations,
            )
        )

    if name == "csegraph_detect_changes":
        from csegraph_core.graph.change_detection import ChangeDetectionService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        base_ref = arguments.get("base_ref", "HEAD~1")
        return to_dict(ChangeDetectionService(db).detect_changes(base_ref=base_ref))

    if name == "csegraph_test_gaps":
        from csegraph_core.graph.test_gaps import TestGapService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        limit = arguments.get("limit", 20)
        return to_dict(TestGapService(db).analyze(limit=limit))

    if name == "csegraph_review_questions":
        from csegraph_core.graph.review_questions import ReviewQuestionsService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        base_ref = arguments.get("base_ref", "HEAD~1")
        return to_dict(ReviewQuestionsService(db).generate(base_ref=base_ref))

    if name == "csegraph_review_eval":
        from csegraph_core.graph.review_eval import ReviewEvalService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        base_ref = arguments.get("base_ref", "HEAD~1")
        ground_truth_ids = arguments["ground_truth_ids"]
        risk_threshold = arguments.get("risk_threshold", "medium")
        return to_dict(ReviewEvalService(db).evaluate(
            ground_truth_ids=ground_truth_ids,
            base_ref=base_ref,
            risk_threshold=risk_threshold,
        ))

    if name == "csegraph_vulnerabilities":
        from csegraph_core.graph.vulnerabilities import VulnerabilityService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        limit = arguments.get("limit", 50)
        return to_dict(VulnerabilityService(db).scan(limit=limit))

    if name == "csegraph_architecture":
        from csegraph_core.graph.architecture import ArchitectureService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        limit = arguments.get("limit", 20)
        return to_dict(ArchitectureService(db).overview(limit=limit))

    if name == "csegraph_flows":
        from csegraph_core.graph.flows import FlowService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        return to_dict(FlowService(db).trace(
            entry_point=arguments.get("entry_point"),
            max_depth=arguments.get("max_depth", 10),
            limit=arguments.get("limit", 20),
        ))

    if name == "csegraph_resolvers":
        from csegraph_core.graph.resolvers import ResolverService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        return to_dict(ResolverService(db).run_all())

    if name == "csegraph_export":
        from csegraph_core.graph.exports import ExportService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        fmt = arguments.get("format", "graphml")
        output = arguments.get("output")
        if not output:
            db_p = Path(db).resolve()
            suffix_map = {"graphml": "csegraph-graph.graphml", "json": "csegraph-export.json", "obsidian": "csegraph-vault"}
            output = str(db_p.with_name(suffix_map.get(fmt, "csegraph-export")))
        return to_dict(ExportService(db).export(output, fmt=fmt))

    raise ValueError(f"Unknown tool: {name}")


def _handle_prompt(name: str, arguments: dict[str, Any] | None = None) -> GetPromptResult:
    args = arguments or {}
    if name == "csegraph-index":
        text = _prompt_text(
            "Build or rebuild the csegraph repository index.",
            [
                "If `repo` is missing, ask the user for the absolute repository path.",
                "Call `csegraph_index` with the repo path and profile (default medium).",
                "Report: files indexed, symbols, edges, cache hits/misses, parse errors.",
                "If parse errors > 0, list them so the user can fix syntax before relying on the graph.",
            ],
            args,
        )
    elif name == "csegraph-refresh":
        text = _prompt_text(
            "Incrementally refresh the csegraph index for changed and deleted files.",
            [
                "If `repo` is missing, ask the user for the absolute repository path.",
                "Call `csegraph_refresh` with the repo path and profile (default medium).",
                "Report: changed files re-indexed, deleted files removed, unchanged files kept.",
                "If parse errors appear on changed files, flag them.",
            ],
            args,
        )
    elif name == "csegraph-minimal":
        text = _prompt_text(
            "Get a compact routing card (~150 tokens) before invoking heavier tools.",
            [
                "If `repo` is missing, ask the user for the absolute repository path.",
                "Call `csegraph_minimal` with the repo and the user's task (if any).",
                "If the routing card includes a stale-index warning, call `csegraph_refresh` before proceeding.",
                "Read the `next_tool_suggestions` array. Call exactly one suggested tool — do not invoke tools not in the suggestions.",
                "If suggestions are empty or the routing card says the graph is sufficient, stop — no further tool calls needed.",
            ],
            args,
        )
    elif name == "csegraph-context":
        text = _prompt_text(
            "Retrieve task-specific context, starting minimal and escalating only when needed.",
            [
                "Step 1: Call `csegraph_minimal` first to get the routing card (skip if already called this session).",
                "Step 2: Call `csegraph_context` with detail_level=auto. Auto returns minimal if sufficient, standard otherwise.",
                "Step 3 (only if needed): If returned_detail_level=minimal and you need source code, re-call with detail_level=standard and a focused target.",
                "Step 4 (only if needed): If a structural dependency question remains, call `csegraph_graph` for one key symbol with depth=1.",
                "Stop after at most 3 tool calls total. Use the returned sufficiency metrics to decide whether more context is needed.",
                "Do NOT call `csegraph_graph` or `csegraph_path` unless the task specifically requires structural/dependency information.",
            ],
            args,
        )
    elif name == "csegraph-detect-changes":
        text = _prompt_text(
            "Detect changed symbols and score review risk using the csegraph dependency graph.",
            [
                "Step 1: Call `csegraph_detect_changes` with the repo and base_ref (default HEAD~1).",
                "Step 2: Focus review on the `high_risk` symbols first — they have the most callers and least test coverage.",
                "Step 3 (only for high-risk symbols): Call `csegraph_graph` with depth=1 on at most one high-risk symbol to see its full blast radius.",
                "Do NOT call more than 2 tools total.",
                "Output: Summarize the risk breakdown (high/medium/low counts), then list each high-risk symbol with its risk factors.",
            ],
            args,
        )
    elif name == "csegraph-test-gaps":
        text = _prompt_text(
            "Identify untested symbols and coverage hotspots in the codebase.",
            [
                "Call `csegraph_test_gaps` with the repo path.",
                "Report overall coverage percentage and the top untested hotspots.",
                "For each hotspot, explain why it is high-priority (caller count, cross-community edges).",
                "If community coverage data is available, highlight communities below 50% coverage.",
                "Do NOT call more than 1 tool.",
            ],
            args,
        )
    elif name == "csegraph-review-questions":
        text = _prompt_text(
            "Generate targeted review questions from change detection and graph structure.",
            [
                "Call `csegraph_review_questions` with the repo path and base_ref.",
                "Present each question grouped by priority (P1 first, then P2, then P3).",
                "For each question, note the related symbols and category.",
                "Do NOT call more than 1 tool.",
            ],
            args,
        )
    elif name == "csegraph-review-eval":
        text = _prompt_text(
            "Evaluate review intelligence precision and recall against known-risky symbols.",
            [
                "Call `csegraph_review_eval` with the repo path, ground_truth_ids, base_ref, and risk_threshold.",
                "Report overall precision, recall, and F1.",
                "List missed symbols and false alarms.",
                "Report question coverage percentage.",
                "Do NOT call more than 1 tool.",
            ],
            args,
        )
    elif name == "csegraph-vulnerabilities":
        text = _prompt_text(
            "Scan the codebase for security vulnerabilities using the dependency graph.",
            [
                "Call `csegraph_vulnerabilities` with the repo path.",
                "Report findings grouped by severity (CRITICAL first, then HIGH, MEDIUM, LOW, INFO).",
                "For each vulnerability, explain the category, affected symbol, evidence, and recommended fix.",
                "If critical or high findings exist, recommend immediate action items.",
                "Do NOT call more than 1 tool.",
            ],
            args,
        )
    elif name == "csegraph-flows":
        text = _prompt_text(
            "Trace execution flows from entry points through the call graph.",
            [
                "Call `csegraph_flows` with the repo path.",
                "If the user asks about a specific function, pass it as entry_point.",
                "Report flows sorted by criticality — higher criticality means more files, deeper chains, less test coverage.",
                "For each high-criticality flow, explain the entry point, what it reaches, and which factors raised the score.",
                "Highlight flows that touch security-sensitive code or cross multiple communities.",
                "Do NOT call more than 1 tool.",
            ],
            args,
        )
    elif name == "csegraph-resolvers":
        text = _prompt_text(
            "Run resolver passes to enrich the graph with inferred edges.",
            [
                "Call `csegraph_resolvers` with the repo path.",
                "Report the total inferred edges added and per-resolver breakdown.",
                "Transitive test edges extend test coverage tracking beyond direct calls.",
                "Python import resolver retries unresolved imports via __init__.py and suffix matching.",
                "TypeScript alias resolver uses tsconfig.json paths to resolve aliased imports.",
                "Do NOT call more than 1 tool.",
            ],
            args,
        )
    elif name == "csegraph-export":
        text = _prompt_text(
            "Export the csegraph index to an external format for browsing or visualization.",
            [
                "Call `csegraph_export` with the repo path and desired format (graphml, obsidian, or json).",
                "Report the output path and the number of nodes/edges exported.",
                "For obsidian, mention that the vault can be opened directly in Obsidian for linked browsing.",
                "For graphml, mention that the file can be imported into Neo4j, Gephi, or yEd.",
                "Do NOT call more than 1 tool.",
            ],
            args,
        )
    elif name == "csegraph-architecture":
        text = _prompt_text(
            "Generate an architecture overview with community summaries and coupling analysis.",
            [
                "Call `csegraph_architecture` with the repo path.",
                "Present each community with its label, size, key symbols, and language breakdown.",
                "Highlight high-coupling pairs between communities as potential architectural concerns.",
                "If warnings mention high coupling, recommend reviewing the dependency direction.",
                "Do NOT call more than 1 tool.",
            ],
            args,
        )
    elif name == "csegraph-review":
        text = _prompt_text(
            "Review current changes using change detection, context, and graph inspection.",
            [
                "Step 1: Call `csegraph_detect_changes` to get the risk-prioritized list of changed symbols.",
                "Step 2: Call `csegraph_review_questions` to get targeted review questions from graph structure.",
                "Step 3 (only if needed): Call `csegraph_context` with detail_level=auto and a task describing the review focus. If high-risk symbols were detected, pass the highest-risk one as target.",
                "Do NOT call more than 3 tools total.",
                "Output: List findings ordered by severity (blockers first, then warnings, then notes). Each finding must reference a file path and symbol name. Include the generated review questions.",
                "If confidence_breakdown shows many INFERRED edges, note that some connections are heuristic and may need manual verification.",
            ],
            args,
        )
    elif name == "csegraph-pre-merge":
        text = _prompt_text(
            "Run a pre-merge checklist using change detection, context, and structural checks.",
            [
                "Step 1: Call `csegraph_refresh` to ensure the index reflects the latest changes.",
                "Step 2: Call `csegraph_detect_changes` with the base branch to get the risk-prioritized change list.",
                "Step 3: Call `csegraph_test_gaps` to check test coverage of changed areas, `csegraph_review_questions` for targeted review questions, or `csegraph_vulnerabilities` for security issues.",
                "Do NOT call more than 3 tools total.",
                "Output a GO / NO-GO recommendation with:",
                "  - Blockers: missing tests, broken call chains, unresolved symbols.",
                "  - Risks: high-degree symbols modified, cross-community edges, INFERRED-confidence connections.",
                "  - Verification: specific test commands or manual checks the reviewer should run.",
            ],
            args,
        )
    else:
        raise ValueError(f"Unknown prompt: {name}")

    return GetPromptResult(
        description=f"CseGraph workflow prompt: {name}",
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(type="text", text=text),
            )
        ],
    )


def _prompt_text(goal: str, steps: list[str], arguments: dict[str, Any]) -> str:
    args_text = json.dumps(arguments, sort_keys=True)
    lines = [
        _TOKEN_EFFICIENCY_PREAMBLE,
        "",
        goal,
        "",
        f"Arguments: {args_text}",
        "",
        "Workflow:",
    ]
    lines.extend(f"- {step}" for step in steps)
    return "\n".join(lines)


ALL_TOOL_NAMES = [t.name for t in _TOOLS]


def create_server(*, allowed_tools: list[str] | None = None) -> Server:
    if allowed_tools is not None:
        unknown = set(allowed_tools) - {t.name for t in _TOOLS}
        if unknown:
            raise ValueError(f"Unknown tool names in --tools filter: {sorted(unknown)}")
        tools = [t for t in _TOOLS if t.name in allowed_tools]
    else:
        tools = list(_TOOLS)

    server = Server("csegraph")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return tools

    @server.list_prompts()
    async def list_prompts() -> list[Prompt]:
        return _PROMPTS

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
        return _handle_prompt(name, dict(arguments or {}))

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            result = _handle_tool(name, arguments)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            error_payload = {"error": str(exc), "tool": name}
            return [TextContent(
                type="text",
                text=json.dumps(error_payload, indent=2),
            )]

    return server


async def run_stdio(*, allowed_tools: list[str] | None = None) -> None:
    import sys
    server = create_server(allowed_tools=allowed_tools)
    if allowed_tools:
        print(f"csegraph MCP server running on stdio — exposing {len(allowed_tools)} tools", file=sys.stderr, flush=True)
    else:
        print("csegraph MCP server running on stdio — waiting for client connection...", file=sys.stderr, flush=True)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
