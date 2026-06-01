from __future__ import annotations

from pathlib import Path
from typing import Any


def build_mcp_server_entry(
    repo: str | Path,
    *,
    command: str = "csegraph",
    vscode_style: bool = False,
) -> dict[str, Any]:
    """Resolve a project-local MCP stdio server entry with an executable that exists.

    When ``command`` is the default ``csegraph``, prefer ``<repo>/env/bin/csegraph`` or
    ``<repo>/env/bin/python -m csegraph_cli serve`` so Claude Code / Cursor do not
    depend on a global PATH install.
    """
    repo_path = Path(repo).resolve()
    resolved_command = command
    args = ["serve"]

    if command == "csegraph":
        venv_cli = repo_path / "env" / "bin" / "csegraph"
        venv_python = repo_path / "env" / "bin" / "python"
        if venv_cli.is_file():
            resolved_command = "env/bin/csegraph"
        elif venv_python.is_file():
            resolved_command = "env/bin/python"
            args = ["-m", "csegraph_cli", "serve"]

    entry: dict[str, Any] = {"command": resolved_command, "args": args}
    if vscode_style:
        entry = {"type": "stdio", **entry}
    else:
        entry["type"] = "stdio"
    return entry
