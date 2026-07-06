from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from csegraph._core.core.serializer import to_dict
from csegraph._core.server.mcp_surface import is_blocking_mcp_tool
from csegraph._core.server.tools import CORE_MCP_TOOL_NAMES, CORE_TOOL_NAMES, TOOLS

logger = logging.getLogger("csegraph.mcp")

__all__ = [
    "CORE_TOOL_NAMES",
    "create_server",
    "run_stdio",
]


def _db_path(repo: str) -> str:
    return str(Path(repo).resolve() / ".csegraph" / "index.db")


def _validate_tool_arguments(name: str, arguments: dict[str, Any]) -> None:
    tool = next(tool for tool in TOOLS if tool.name == name)
    schema = tool.inputSchema
    properties = set((schema.get("properties") or {}).keys())
    unknown = sorted(set(arguments) - properties)
    if unknown:
        raise ValueError(f"Unknown arguments for {name}: {', '.join(unknown)}")
    missing = sorted(set(schema.get("required") or ()) - set(arguments))
    if missing:
        raise ValueError(f"Missing required arguments for {name}: {', '.join(missing)}")


def _handle_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    bound_repo: str | None = None,
    host_platform: str | None = None,
) -> Any:
    del host_platform
    if name not in CORE_MCP_TOOL_NAMES:
        raise ValueError(f"Unknown tool: {name}")
    if bound_repo and "repo" not in arguments:
        arguments = {**arguments, "repo": bound_repo}
    _validate_tool_arguments(name, arguments)
    return _dispatch_tool(name, arguments)


def _dispatch_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == "csegraph_index":
        from csegraph._core.index.services import IndexService

        repo = arguments["repo"]
        return to_dict(IndexService(_db_path(repo)).index(repo))

    if name == "csegraph_refresh":
        from csegraph._core.retrieval.freshness import FreshnessCoordinator

        repo = arguments["repo"]
        return to_dict(FreshnessCoordinator(_db_path(repo)).explicit_refresh(repo))

    if name == "csegraph_minimal":
        from csegraph._core.retrieval.minimal import MinimalService

        repo = arguments["repo"]
        return to_dict(
            MinimalService(_db_path(repo)).first(
                task=arguments.get("task"),
                repo=repo,
            )
        )

    if name == "csegraph_context":
        from csegraph._core.core.models import ContextRequest
        from csegraph._core.retrieval.context import ContextService

        repo = arguments["repo"]
        return to_dict(
            ContextService(_db_path(repo)).retrieve(
                ContextRequest(
                    repo=repo,
                    task=arguments["task"],
                    target=arguments.get("target"),
                    task_kind=arguments.get("task_kind", "auto"),
                    token_budget=arguments.get("token_budget", 800),
                    source_mode=arguments.get("source_mode", "auto"),
                    diagnostic=arguments.get("diagnostic", False),
                )
            )
        )

    if name == "csegraph_graph":
        from csegraph._core.graph.queries import GraphQueryService

        repo = arguments["repo"]
        return to_dict(
            GraphQueryService(_db_path(repo)).neighborhood(
                arguments["node"],
                depth=arguments.get("depth", 1),
                relations=arguments.get("relations"),
                confidence_tiers=arguments.get("confidence_tiers"),
            )
        )

    if name == "csegraph_path":
        from csegraph._core.graph.queries import GraphQueryService

        repo = arguments["repo"]
        return to_dict(
            GraphQueryService(_db_path(repo)).shortest_path(
                arguments["source"],
                arguments["target"],
                relations=arguments.get("relations"),
                confidence_tiers=arguments.get("confidence_tiers"),
            )
        )

    raise ValueError(f"Unknown tool: {name}")


_SERVER_INSTRUCTIONS = (
    "CseGraph is a local-first context engine. Call csegraph_context directly for "
    "ordinary coding tasks. Use csegraph_minimal only for index health or orientation, "
    "then graph/path only for focused structural expansion."
)


def create_server(
    *,
    allowed_tools: list[str] | None = None,
    bound_repo: str | None = None,
    host_platform: str | None = None,
) -> Server:
    allowed_tools = CORE_TOOL_NAMES if allowed_tools is None else allowed_tools
    unknown = set(allowed_tools) - CORE_MCP_TOOL_NAMES
    if unknown:
        raise ValueError(f"Unknown tool names in --tools filter: {sorted(unknown)}")
    allowed = set(allowed_tools)
    tools = [tool for tool in TOOLS if tool.name in allowed]
    instructions = _SERVER_INSTRUCTIONS
    if bound_repo:
        instructions += f" This server is bound to repo: {bound_repo}."
    server = Server("csegraph", instructions=instructions)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent] | CallToolResult:
        try:
            if name not in allowed:
                raise ValueError(f"Tool '{name}' is not enabled for this server")
            if is_blocking_mcp_tool(name):
                result = await asyncio.to_thread(
                    _handle_tool,
                    name,
                    arguments,
                    bound_repo=bound_repo,
                    host_platform=host_platform,
                )
            else:
                result = _handle_tool(
                    name,
                    arguments,
                    bound_repo=bound_repo,
                    host_platform=host_platform,
                )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            to_payload = getattr(exc, "to_payload", None)
            payload = to_payload() if callable(to_payload) else {"error": str(exc)}
            payload["tool"] = name
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(payload, indent=2),
                    )
                ],
                isError=True,
            )

    return server


async def run_stdio(
    *,
    allowed_tools: list[str] | None = None,
    bound_repo: str | None = None,
    host_platform: str | None = None,
) -> None:
    server = create_server(
        allowed_tools=allowed_tools,
        bound_repo=bound_repo,
        host_platform=host_platform,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
