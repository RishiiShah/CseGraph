from __future__ import annotations

import os
from pathlib import Path

from csegraph_core.mcp_resolve import build_mcp_server_entry


def test_build_entry_uses_project_venv_cli(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cli = repo / "env" / "bin" / "csegraph"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(cli, 0o755)

    entry = build_mcp_server_entry(repo)

    assert entry == {
        "type": "stdio",
        "command": "env/bin/csegraph",
        "args": ["serve"],
    }


def test_build_entry_falls_back_to_python_module(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    py = repo / "env" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")

    entry = build_mcp_server_entry(repo)

    assert entry["command"] == "env/bin/python"
    assert entry["args"] == ["-m", "csegraph_cli", "serve"]


def test_custom_command_is_not_rewritten(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    entry = build_mcp_server_entry(repo, command="my-csegraph")

    assert entry["command"] == "my-csegraph"
    assert entry["args"] == ["serve"]
