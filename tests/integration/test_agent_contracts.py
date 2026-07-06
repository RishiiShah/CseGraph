from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from csegraph._core.server.tools import TOOLS

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_COMMANDS = {
    "index",
    "refresh",
    "context",
    "graph",
    "path",
    "status",
    "doctor",
    "install",
    "serve",
}


def _help(*args: str) -> str:
    command = args or ("--help",)
    return subprocess.run(
        [sys.executable, "-m", "csegraph._cli", *command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_public_cli_is_exactly_the_nine_command_surface():
    output = _help()
    match = re.search(r"\{(?P<commands>[a-z0-9_,.-]+)\}", output)
    assert match
    assert set(match.group("commands").split(",")) == PUBLIC_COMMANDS

    for command in PUBLIC_COMMANDS:
        assert f"usage: csegraph {command}" in _help(command, "--help")


def test_context_help_has_only_v5_options():
    output = _help("context", "--help")
    for option in (
        "--repo",
        "--target",
        "--task-kind",
        "--token-budget",
        "--source-mode",
        "--diagnostic",
        "--format",
    ):
        assert option in output
    for removed in (
        "--profile",
        "--config",
        "--encoding",
        "--max-bytes",
        "--cursor",
        "--engine",
        "--legacy",
        "--explain",
        "--detail-level",
        "--response-mode",
    ):
        assert removed not in output


def test_runtime_dependencies_and_mcp_docs_match_contract():
    import tomllib

    with (ROOT / "pyproject.toml").open("rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]
    names = {
        dependency.split("<", 1)[0].split(">", 1)[0].split("=", 1)[0] for dependency in dependencies
    }
    assert names == {
        "mcp",
        "tree-sitter",
        "tree-sitter-python",
        "tree-sitter-javascript",
        "tree-sitter-typescript",
    }

    documented = (ROOT / "docs" / "csegraph.md").read_text(encoding="utf-8")
    tool_names = {tool.name for tool in TOOLS}
    assert set(re.findall(r"^### `(csegraph_[a-z_]+)`$", documented, re.MULTILINE)) == tool_names
