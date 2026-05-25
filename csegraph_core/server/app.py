from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, GetPromptResult, Prompt, PromptArgument, PromptMessage, TextContent, Tool

from csegraph_core.core.models import to_dict
from csegraph_core.server.session import _SESSION

logger = logging.getLogger("csegraph.mcp")

_MIN_BYTE_CAP = 256

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
                    "minimum": _MIN_BYTE_CAP,
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
                "confidence_tiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional confidence tier filter (e.g. ['EXTRACTED']). BFS only follows edges with these tiers. Default: all tiers.",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": _MIN_BYTE_CAP,
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
                "confidence_tiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional confidence tier filter (e.g. ['EXTRACTED']). BFS only follows edges with these tiers. Default: all tiers.",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": _MIN_BYTE_CAP,
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

_CORE_MCP_TOOL_NAMES = (
    "csegraph_index",
    "csegraph_refresh",
    "csegraph_minimal",
    "csegraph_context",
    "csegraph_graph",
    "csegraph_path",
)
_TOOLS = [tool for tool in _TOOLS if tool.name in _CORE_MCP_TOOL_NAMES]

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
        name="csegraph-debug-issue",
        title="Debug Issue",
        description=(
            "Guided debugging using the routing card, task context, and one graph neighborhood. "
            "Replaces broad grep and full-file reads."
        ),
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(
                name="description",
                description="Bug symptom, error message, or failing behavior.",
                required=True,
            ),
            PromptArgument(
                name="target",
                description="Optional failing symbol, file path, or node ID.",
                required=False,
            ),
        ],
    ),
    Prompt(
        name="csegraph-review-changes",
        title="Review Changes",
        description=(
            "Pre-commit review workflow using graph-backed context. "
            "Use terminal `csegraph analyze` for git-scoped risk and diagnostics (CLI-only)."
        ),
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(
                name="task",
                description="What changed or what to review (e.g. auth module edits).",
                required=True,
            ),
            PromptArgument(
                name="base",
                description="Optional git base ref for human CLI diff commands (default HEAD~1).",
                required=False,
            ),
        ],
    ),
    Prompt(
        name="csegraph-pre-merge-check",
        title="Pre-Merge Check",
        description=(
            "PR readiness using minimal routing, task context, and optional dependency inspection. "
            "Stays within the six core MCP tools."
        ),
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(
                name="task",
                description="Merge or PR summary (branch purpose, risky areas).",
                required=True,
            ),
        ],
    ),
    Prompt(
        name="csegraph-explore-architecture",
        title="Explore Architecture",
        description=(
            "Map subsystem structure with a routing card and hub-aware graph neighborhood. "
            "For human HTML exports use CLI `csegraph export --format html`."
        ),
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(
                name="focus",
                description="Optional subsystem, symbol, or area to explore.",
                required=False,
            ),
        ],
    ),
    Prompt(
        name="csegraph-onboard-developer",
        title="Onboard Developer",
        description=(
            "Orient a new contributor: routing card, overview context, and one structural graph call."
        ),
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(
                name="focus",
                description="Optional area of interest (e.g. retrieval, MCP server).",
                required=False,
            ),
        ],
    ),
]

_CORE_MCP_PROMPT_NAMES = (
    "csegraph-index",
    "csegraph-refresh",
    "csegraph-minimal",
    "csegraph-context",
    "csegraph-debug-issue",
    "csegraph-review-changes",
    "csegraph-pre-merge-check",
    "csegraph-explore-architecture",
    "csegraph-onboard-developer",
)
_PROMPTS = [prompt for prompt in _PROMPTS if prompt.name in _CORE_MCP_PROMPT_NAMES]

_PROMPT_TOOL_DEPENDENCIES: dict[str, set[str]] = {
    "csegraph-index": {"csegraph_index"},
    "csegraph-refresh": {"csegraph_refresh"},
    "csegraph-minimal": {"csegraph_minimal"},
    "csegraph-context": {"csegraph_minimal", "csegraph_context", "csegraph_graph"},
    "csegraph-debug-issue": {"csegraph_minimal", "csegraph_context", "csegraph_graph"},
    "csegraph-review-changes": {"csegraph_refresh", "csegraph_minimal", "csegraph_context"},
    "csegraph-pre-merge-check": {"csegraph_minimal", "csegraph_context", "csegraph_graph"},
    "csegraph-explore-architecture": {"csegraph_minimal", "csegraph_graph"},
    "csegraph-onboard-developer": {"csegraph_minimal", "csegraph_context", "csegraph_graph"},
}


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
    if name not in _CORE_MCP_TOOL_NAMES:
        raise ValueError(f"Unknown tool: {name}")
    provided_max = arguments.get("max_bytes")
    if provided_max is not None:
        if isinstance(provided_max, float) and provided_max == int(provided_max):
            provided_max = int(provided_max)
        if not isinstance(provided_max, int):
            raise TypeError(f"max_bytes must be an integer, got {type(provided_max).__name__}")
    if isinstance(provided_max, int) and 0 < provided_max < _MIN_BYTE_CAP:
        raise ValueError(f"max_bytes must be at least {_MIN_BYTE_CAP}")
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
        effective_max = provided_max if isinstance(provided_max, int) and provided_max > 0 else None
        _apply_byte_cap(result, effective_max)
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
            _mark_truncated(truncated, "source_text")
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
            _mark_truncated(truncated, "explanation")
            if _encoded_size(result) <= max_bytes:
                result["byte_cap_applied"] = True
                _finalize_response_bytes(result)
                return

    # Step 3: trim nodes list (lowest-priority assumed at tail).
    if isinstance(nodes, list) and nodes:
        while len(nodes) > 1 and _encoded_size(result) > max_bytes:
            _pop_omitted(result, "nodes")
        if "nodes" in result.get("omitted_counts", {}):
            _mark_truncated(truncated, "nodes")
            if _encoded_size(result) <= max_bytes:
                result["byte_cap_applied"] = True
                _finalize_response_bytes(result)
                return

    # Step 4: trim edges list.
    edges = result.get("edges")
    if isinstance(edges, list) and edges:
        while edges and _encoded_size(result) > max_bytes:
            _pop_omitted(result, "edges")
        if "edges" in result.get("omitted_counts", {}):
            _mark_truncated(truncated, "edges")
            if _encoded_size(result) <= max_bytes:
                result["byte_cap_applied"] = True
                _finalize_response_bytes(result)
                return

    # Step 5: trim known non-node result shapes in deterministic priority order.
    for key in ("low_risk", "medium_risk", "high_risk", "flows"):
        if _encoded_size(result) <= max_bytes:
            break
        _trim_list_field(result, key, max_bytes, truncated)

    # Step 6 (generic): trim any remaining list-valued payload keys.
    if _encoded_size(result) > max_bytes:
        _generic_list_trim(result, max_bytes, truncated)

    if _encoded_size(result) > max_bytes:
        _final_compact_to_cap(result, max_bytes, truncated)

    result["byte_cap_applied"] = bool(truncated)
    _finalize_response_bytes(result)
    if result["response_bytes"] > max_bytes:
        _replace_with_minimal_cap_notice(result, max_bytes, truncated)
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


_TRIM_SKIP_KEYS = frozenset({
    "truncated_fields",
    "tools_already_called",
    "warnings",
    "omitted_counts",
})


def _mark_truncated(truncated: list[str], key: str) -> None:
    if key not in truncated:
        truncated.append(key)


def _pop_omitted(result: dict[str, Any], key: str) -> None:
    items = result.get(key)
    if not isinstance(items, list) or not items:
        return
    items.pop()
    counts = result.setdefault("omitted_counts", {})
    counts[key] = counts.get(key, 0) + 1


def _trim_list_field(
    result: dict[str, Any],
    key: str,
    max_bytes: int,
    truncated: list[str],
    *,
    min_items: int = 0,
) -> None:
    items = result.get(key)
    if not isinstance(items, list):
        return
    before = len(items)
    while len(items) > min_items and _encoded_size(result) > max_bytes:
        _pop_omitted(result, key)
    if len(items) != before:
        _mark_truncated(truncated, key)


def _generic_list_trim(
    result: dict[str, Any], max_bytes: int, truncated: list[str]
) -> None:
    """Trim list-valued payload keys deterministically until under budget."""
    while _encoded_size(result) > max_bytes:
        candidates = [
            k for k, v in result.items()
            if isinstance(v, list) and v and k not in _TRIM_SKIP_KEYS
        ]
        if not candidates:
            break
        largest_key = max(candidates, key=lambda k: len(result[k]))
        _pop_omitted(result, largest_key)
        _mark_truncated(truncated, largest_key)


def _final_compact_to_cap(
    result: dict[str, Any], max_bytes: int, truncated: list[str]
) -> None:
    """Last-resort compaction that keeps cap metadata and drops payload bulk."""
    for key, value in list(result.items()):
        if _encoded_size(result) <= max_bytes:
            return
        if key in _TRIM_SKIP_KEYS:
            continue
        if isinstance(value, list) and value:
            counts = result.setdefault("omitted_counts", {})
            counts[key] = counts.get(key, 0) + len(value)
            result[key] = []
            _mark_truncated(truncated, key)

    for preferred in ("summary", "message", "error"):
        if _encoded_size(result) <= max_bytes:
            return
        _truncate_string_field(result, preferred, max_bytes, truncated)

    for key, value in list(result.items()):
        if _encoded_size(result) <= max_bytes:
            return
        if isinstance(value, str) and key not in {"command", "byte_cap"}:
            _truncate_string_field(result, key, max_bytes, truncated)

    for key in ("warnings", "tools_already_called"):
        if _encoded_size(result) <= max_bytes:
            return
        value = result.get(key)
        if isinstance(value, list) and value:
            counts = result.setdefault("omitted_counts", {})
            counts[key] = counts.get(key, 0) + len(value)
            result[key] = []
            _mark_truncated(truncated, key)


def _truncate_string_field(
    result: dict[str, Any], key: str, max_bytes: int, truncated: list[str]
) -> None:
    value = result.get(key)
    if not isinstance(value, str) or not value:
        return
    while value and _encoded_size(result) > max_bytes:
        excess = _encoded_size(result) - max_bytes
        keep = max(0, len(value) - excess - 16)
        value = value[:keep]
        result[key] = value + ("..." if keep > 0 else "")
    _mark_truncated(truncated, key)


def _replace_with_minimal_cap_notice(
    result: dict[str, Any], max_bytes: int, truncated: list[str]
) -> None:
    counts = result.get("omitted_counts", {})
    omitted_total = sum(v for v in counts.values() if isinstance(v, int))
    command = result.get("command")
    truncated_snapshot = list(truncated) or ["response"]
    result.clear()
    if command:
        result["command"] = command
    result["byte_cap"] = max_bytes
    result["byte_cap_applied"] = True
    result["truncated_fields"] = truncated_snapshot
    if counts:
        result["omitted_counts"] = counts
    result["summary"] = "Response compacted to satisfy max_bytes."
    result["response_bytes"] = max_bytes
    if _encoded_size(result) <= max_bytes:
        return

    result["truncated_fields"] = ["response"]
    if omitted_total:
        result["omitted_counts"] = {"response": omitted_total}
    result["summary"] = "Response compacted to satisfy max_bytes."
    if _encoded_size(result) <= max_bytes:
        return

    result.pop("summary", None)
    if _encoded_size(result) <= max_bytes:
        return

    result.pop("command", None)


def _dispatch_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == "csegraph_index":
        from csegraph_core.index.services import IndexService
        from csegraph_core.postprocess import PostprocessService
        from csegraph_core.graph.queries import clear_hub_cache

        repo = arguments["repo"]
        profile = arguments.get("profile", "medium")
        db = _db_path(repo, arguments.get("db"))
        result = IndexService(db).index(repo, profile=profile)
        pp_level = arguments.get("postprocess_level", "full")
        if pp_level != "none":
            PostprocessService(db).postprocess(level=pp_level)
        clear_hub_cache()
        return to_dict(result)

    if name == "csegraph_refresh":
        from csegraph_core.index.services import RefreshService
        from csegraph_core.postprocess import PostprocessService
        from csegraph_core.graph.queries import clear_hub_cache

        repo = arguments["repo"]
        profile = arguments.get("profile", "medium")
        db = _db_path(repo, arguments.get("db"))
        result = RefreshService(db).refresh(profile=profile)
        pp_level = arguments.get("postprocess_level", "full")
        if pp_level != "none" and result.files_indexed > 0:
            PostprocessService(db).postprocess(level=pp_level)
        clear_hub_cache()
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
        confidence_tiers = arguments.get("confidence_tiers")
        return to_dict(
            GraphQueryService(db).neighborhood(
                arguments["node"],
                depth=depth,
                detail_level=detail_level,
                relations=relations,
                confidence_tiers=confidence_tiers,
            )
        )

    if name == "csegraph_path":
        from csegraph_core.graph.queries import GraphQueryService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        detail_level = arguments.get("detail_level", "minimal")
        relations = arguments.get("relations")
        confidence_tiers = arguments.get("confidence_tiers")
        return to_dict(
            GraphQueryService(db).shortest_path(
                arguments["source"],
                arguments["target"],
                detail_level=detail_level,
                relations=relations,
                confidence_tiers=confidence_tiers,
            )
        )

    raise ValueError(f"Unknown tool: {name}")


def _handle_prompt(name: str, arguments: dict[str, Any] | None = None) -> GetPromptResult:
    if name not in _CORE_MCP_PROMPT_NAMES:
        raise ValueError(f"Unknown prompt: {name}")
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
    elif name == "csegraph-debug-issue":
        text = _prompt_text(
            "Debug a reported issue using graph-backed context instead of repo-wide search.",
            [
                "If `repo` is missing, ask for the absolute repository path.",
                "Step 1: Call `csegraph_minimal` with task set to the issue description.",
                "If the routing card warns the index is stale, call `csegraph_refresh` (counts toward the 3-call limit).",
                "Step 2: Call `csegraph_context` with task=description, target if provided, detail_level=auto.",
                "Step 3 (only if needed): Call `csegraph_graph` on the failing symbol with depth=1 and detail_level=minimal.",
                "Do not use broad grep or read whole files unless context is insufficient after these steps.",
                "Stop after at most 3 csegraph MCP tool calls.",
            ],
            args,
        )
    elif name == "csegraph-review-changes":
        base = args.get("base", "HEAD~1")
        text = _prompt_text(
            "Review recent changes using compact graph context (context-engine workflow).",
            [
                "If `repo` is missing, ask for the absolute repository path.",
                "Optional (human terminal, not MCP): run `csegraph analyze --base-ref "
                + repr(base)
                + "` for risk-ranked diagnostics.",
                "Step 1: Call `csegraph_refresh` so the index matches working tree.",
                "Step 2: Call `csegraph_minimal` with the review task.",
                "Step 3: Call `csegraph_context` with detail_level=auto and targets from the change list or task.",
                "Prefer graph-backed context over reading entire changed files.",
                "Stop after at most 3 csegraph MCP tool calls.",
            ],
            args,
        )
    elif name == "csegraph-pre-merge-check":
        text = _prompt_text(
            "Assess merge/PR readiness with minimal context cost.",
            [
                "If `repo` is missing, ask for the absolute repository path.",
                "Step 1: Call `csegraph_minimal` with the merge/PR task summary.",
                "Step 2: Call `csegraph_context` with detail_level=auto on the highest-risk areas mentioned.",
                "Step 3 (only if needed): Call `csegraph_graph` with depth=1 on one critical symbol.",
                "Report: sufficiency metrics, stale-index warnings, and whether more context is needed.",
                "Do not invoke review-only MCP tools; use CLI diagnostics only if the user asks.",
                "Stop after at most 3 csegraph MCP tool calls.",
            ],
            args,
        )
    elif name == "csegraph-explore-architecture":
        focus = args.get("focus") or "a high-degree key entity from the routing card"
        text = _prompt_text(
            "Explore repository architecture with a routing card and one graph neighborhood.",
            [
                "If `repo` is missing, ask for the absolute repository path.",
                "Step 1: Call `csegraph_minimal` (task may mention the focus area).",
                "Step 2: Call `csegraph_graph` on "
                + repr(focus)
                + " with depth=2 and detail_level=minimal; use relations=[\"calls\",\"imports\"] if exploring dependencies.",
                "Summarize modules, coupling hints from confidence_breakdown/hubs_skipped, and suggested next targets.",
                "Stop after at most 3 csegraph MCP tool calls (second call may be another graph if focus was wrong).",
            ],
            args,
        )
    elif name == "csegraph-onboard-developer":
        text = _prompt_text(
            "Onboard a developer to the codebase using graph-backed orientation.",
            [
                "If `repo` is missing, ask for the absolute repository path.",
                "Step 1: Call `csegraph_minimal` to surface key entities and languages.",
                "Step 2: Call `csegraph_context` with task describing onboarding goals and optional focus area.",
                "Step 3: Call `csegraph_graph` on one key entity at depth=1 for structural orientation.",
                "Produce a short guide: entry symbols, main languages, and where to pull task context next.",
                "Stop after at most 3 csegraph MCP tool calls.",
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


CORE_TOOL_NAMES = list(_CORE_MCP_TOOL_NAMES)


def _prompts_for_tools(allowed_tool_names: set[str]) -> list[Prompt]:
    return [
        prompt for prompt in _PROMPTS
        if _PROMPT_TOOL_DEPENDENCIES.get(prompt.name, set()).issubset(allowed_tool_names)
    ]


def create_server(*, allowed_tools: list[str] | None = None) -> Server:
    if allowed_tools is None:
        allowed_tools = CORE_TOOL_NAMES
    unknown = set(allowed_tools) - {t.name for t in _TOOLS}
    if unknown:
        raise ValueError(f"Unknown tool names in --tools filter: {sorted(unknown)}")
    allowed_tool_names = set(allowed_tools)
    tools = [t for t in _TOOLS if t.name in allowed_tools]
    prompts = _prompts_for_tools(allowed_tool_names)
    allowed_prompt_names = {prompt.name for prompt in prompts}

    server = Server("csegraph")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return tools

    @server.list_prompts()
    async def list_prompts() -> list[Prompt]:
        return prompts

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
        if name not in allowed_prompt_names:
            raise ValueError(f"Prompt '{name}' is not enabled for this server")
        return _handle_prompt(name, dict(arguments or {}))

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent] | CallToolResult:
        try:
            if name not in allowed_tool_names:
                raise ValueError(f"Tool '{name}' is not enabled for this server")
            result = _handle_tool(name, arguments)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            error_payload = {"error": str(exc), "tool": name}
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=json.dumps(error_payload, indent=2),
                )],
                isError=True,
            )

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
