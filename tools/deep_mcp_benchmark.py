"""Compatibility wrapper for the native MCP sandbox deep benchmark."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    env = dict(os.environ)
    env.setdefault("CSEGRAPH_100_QUERY_LIMIT", "6")
    env.setdefault("CSEGRAPH_100_ITERATIONS", "5")
    env.setdefault(
        "CSEGRAPH_100_QUERIES_REPORT",
        str(REPO_ROOT / "benchmark_results" / "native_mcp_deep.md"),
    )
    print("Delegating to tools/run_100_queries_benchmark.py using native MCP stdio.", flush=True)
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "run_100_queries_benchmark.py")],
        env=env,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
