#!/usr/bin/env python
"""Repo-local maintainer CLI for CseGraph development and analytics tools."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CLI_SRC = ROOT / "packages" / "csegraph-cli"
if str(CLI_SRC) not in sys.path:
    sys.path.insert(0, str(CLI_SRC))

from csegraph_cli.main import dev_main


if __name__ == "__main__":
    raise SystemExit(dev_main())
