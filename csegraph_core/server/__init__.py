"""csegraph MCP stdio server.

Exposes csegraph's core capabilities (index, refresh, context, graph, report)
as MCP tools over the stdio transport, so coding agents can call them natively.
"""

def __getattr__(name: str):
    if name in ("create_server", "run_stdio"):
        from csegraph_core.server.app import create_server, run_stdio  # noqa: F811
        return create_server if name == "create_server" else run_stdio
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["create_server", "run_stdio"]
