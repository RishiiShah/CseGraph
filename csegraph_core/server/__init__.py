"""csegraph MCP stdio server.

Exposes csegraph's core capabilities (index, refresh, context, graph, report)
as MCP tools over the stdio transport, so coding agents can call them natively.
"""

def __getattr__(name: str):
    if name in ("create_server", "run_stdio", "ALL_TOOL_NAMES"):
        from csegraph_core.server.app import create_server, run_stdio, ALL_TOOL_NAMES  # noqa: F811
        return {"create_server": create_server, "run_stdio": run_stdio, "ALL_TOOL_NAMES": ALL_TOOL_NAMES}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["ALL_TOOL_NAMES", "create_server", "run_stdio"]
