from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from csegraph_core.graph.queries import clear_hub_cache
from csegraph_core.server.session import _SESSION


@pytest.fixture(autouse=True)
def _reset_mcp_session():
    _SESSION.reset()
    clear_hub_cache()
    yield
    _SESSION.reset()
    clear_hub_cache()


@pytest.fixture()
def sample_repo(tmp_path: Path) -> Path:
    """Create a minimal two-file Python repo for integration tests."""
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    (root / "helpers.py").write_text(
        "def clean_name(value: str) -> str:\n    return value.strip().lower()\n",
        encoding="utf-8",
    )
    (root / "service.py").write_text(
        "from helpers import clean_name\n\n"
        "def create_user(name: str) -> dict:\n    return {'name': clean_name(name)}\n",
        encoding="utf-8",
    )
    return root


def run_cli(*args: str) -> dict:
    """Run csegraph_cli as a subprocess and return parsed JSON output."""
    proc = subprocess.run(
        [sys.executable, "-m", "csegraph_cli", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout)


def run_cli_text(*args: str) -> str:
    """Run csegraph_cli as a subprocess and return raw stdout."""
    proc = subprocess.run(
        [sys.executable, "-m", "csegraph_cli", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def run_dev_cli(*args: str) -> dict:
    """Run the repo-local CseGraph maintainer CLI and return parsed JSON output."""
    proc = subprocess.run(
        [sys.executable, "tools/csegraph_dev.py", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout)


def run_dev_cli_text(*args: str) -> str:
    """Run the repo-local CseGraph maintainer CLI and return raw stdout."""
    proc = subprocess.run(
        [sys.executable, "tools/csegraph_dev.py", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout
