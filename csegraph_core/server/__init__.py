"""csegraph MCP stdio server.

Exposes csegraph's core capabilities (index, refresh, context, graph, report)
as MCP tools over the stdio transport, so coding agents can call them natively.
"""

from csegraph_core.server.app import create_server, run_stdio

__all__ = ["create_server", "run_stdio"]
