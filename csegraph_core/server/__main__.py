"""python -m csegraph_core.server — launch the MCP stdio server directly."""

import asyncio

from csegraph_core.server.app import run_stdio

if __name__ == "__main__":
    asyncio.run(run_stdio())
