"""python -m csegraph._core.server — launch the MCP stdio server directly."""

import asyncio

from csegraph._core.server.app import run_stdio

if __name__ == "__main__":
    asyncio.run(run_stdio())
