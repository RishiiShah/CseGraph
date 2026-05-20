from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import GetPromptResult, Prompt, PromptArgument, PromptMessage, TextContent, Tool

from csegraph_core.core.models import to_dict
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
                    "description": "Approximate max tokens for returned context.",
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
                "db": {
                    "type": "string",
                    "description": "SQLite database path. Default: <repo>/.csegraph/index.db",
                },
            },
            "required": ["source", "target", "repo"],
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
        name="csegraph-review",
        title="Review Current Changes",
        description="Review changes with csegraph context, graph inspection, and structural report data.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(name="task", description="Optional review focus.", required=False),
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
    if isinstance(result, dict):
        _apply_session_filter(result)
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


def _dispatch_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == "csegraph_index":
        from csegraph_core.index.services import IndexService

        repo = arguments["repo"]
        profile = arguments.get("profile", "medium")
        db = _db_path(repo, arguments.get("db"))
        return to_dict(IndexService(db).index(repo, profile=profile))

    if name == "csegraph_refresh":
        from csegraph_core.index.services import RefreshService

        repo = arguments["repo"]
        profile = arguments.get("profile", "medium")
        db = _db_path(repo, arguments.get("db"))
        return to_dict(RefreshService(db).refresh(profile=profile))

    if name == "csegraph_minimal":
        from csegraph_core.retrieval.minimal import MinimalService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        return to_dict(MinimalService(db).first(task=arguments.get("task")))

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
        return to_dict(
            GraphQueryService(db).shortest_path(
                arguments["source"],
                arguments["target"],
                detail_level=detail_level,
            )
        )

    raise ValueError(f"Unknown tool: {name}")


def _handle_prompt(name: str, arguments: dict[str, Any] | None = None) -> GetPromptResult:
    args = arguments or {}
    if name == "csegraph-index":
        text = _prompt_text(
            "Build or rebuild the repository index.",
            [
                "If `repo` is missing, ask the user for the absolute repository path.",
                "Call `csegraph_index` with the repo path and optional profile.",
                "Summarize files, symbols, edges, cache stats, and parse errors.",
            ],
            args,
        )
    elif name == "csegraph-refresh":
        text = _prompt_text(
            "Refresh changed and deleted files in the existing index.",
            [
                "If `repo` is missing, ask the user for the absolute repository path.",
                "Call `csegraph_refresh` with the repo path and optional profile.",
                "Summarize changed, deleted, unchanged files, cache stats, and parse errors.",
            ],
            args,
        )
    elif name == "csegraph-minimal":
        text = _prompt_text(
            "Get a compact routing card before invoking heavier tools.",
            [
                "If `repo` is missing, ask the user for the absolute repository path.",
                "Call `csegraph_minimal` with the repo and the user's task (if any).",
                "Use the returned `next_tool_suggestions` to choose the next call; do not invoke unrelated tools.",
            ],
            args,
        )
    elif name == "csegraph-context":
        text = _prompt_text(
            "Retrieve graph-backed context for the task, starting with minimal if sufficient.",
            [
                "If `repo` or `task` is missing, ask for it before calling tools.",
                "Call `csegraph_context` with repo, task, optional target, detail_level=auto to start efficiently.",
                "If returned_detail_level is minimal, optionally request standard for deeper context or source code.",
                "Use the returned nodes, reasons, sufficiency, and token estimates to guide the work.",
            ],
            args,
        )
    elif name == "csegraph-review":
        text = _prompt_text(
            "Review the current work using csegraph before making recommendations.",
            [
                "Call `csegraph_context` with detail_level=auto to start efficiently (returns minimal if sufficient, standard otherwise).",
                "Use `csegraph_graph` for key changed symbols when a neighborhood clarifies blast radius.",
                "Report findings first, ordered by severity, with file and symbol references.",
            ],
            args,
        )
    elif name == "csegraph-pre-merge":
        text = _prompt_text(
            "Run a pre-merge context and risk checklist.",
            [
                "Call `csegraph_refresh` first if the index may be stale.",
                "Call `csegraph_context` with detail_level=auto for the merge or PR task; request standard only when source is needed.",
                "Use `csegraph_path` or `csegraph_graph` for any risky dependency questions.",
                "Return blockers, residual risks, and verification commands.",
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
        goal,
        "",
        f"Arguments: {args_text}",
        "",
        "Workflow:",
    ]
    lines.extend(f"- {step}" for step in steps)
    return "\n".join(lines)


def create_server() -> Server:
    server = Server("csegraph")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return _TOOLS

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


async def run_stdio() -> None:
    import sys
    print("csegraph MCP server running on stdio — waiting for client connection...", file=sys.stderr, flush=True)
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
