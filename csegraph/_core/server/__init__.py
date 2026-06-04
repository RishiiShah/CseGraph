"""csegraph MCP stdio server.

Exposes the core context loop (index, refresh, minimal, context, graph, path)
as MCP tools over the stdio transport, so coding agents can retrieve minimal
task context without broad search or full-file reads.
"""

def __getattr__(name: str):
    _exports = ("create_server", "run_stdio", "CORE_TOOL_NAMES")
    if name in _exports:
        from csegraph._core.server import app as _app  # noqa: F811
        return getattr(_app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["CORE_TOOL_NAMES", "create_server", "run_stdio"]
