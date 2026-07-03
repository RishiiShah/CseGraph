from __future__ import annotations

import shutil
import sys
import sysconfig
from pathlib import Path
from typing import Any


class McpLauncherResolutionError(RuntimeError):
    """Raised when CseGraph cannot build a real CLI MCP launcher."""


def resolve_csegraph_executable(repo: str | Path, *, command: str = "csegraph") -> str:
    """Resolve a concrete ``csegraph`` console script for generated MCP configs.

    Generated MCP client configuration must launch the real CseGraph CLI server.
    It must not point agents at Python snippets, MCP SDK clients, or private
    module entrypoints.  Relative explicit paths are resolved from the target
    repository first so ``--command env/bin/csegraph`` remains convenient.
    """

    repo_path = Path(repo).resolve()
    if _looks_like_path(command):
        for candidate in _path_command_candidates(repo_path, command):
            resolved_path = _first_existing_script(candidate)
            if resolved_path is not None:
                return str(resolved_path.resolve())
        raise McpLauncherResolutionError(
            f"CseGraph MCP command does not exist: {command}. "
            "Install the CLI or pass --command /absolute/path/to/csegraph."
        )

    if command != "csegraph":
        which_result = shutil.which(command)
        if which_result:
            return str(Path(which_result).resolve())
        raise McpLauncherResolutionError(
            f"CseGraph MCP command was not found on PATH: {command}. "
            "Pass --command /absolute/path/to/csegraph."
        )

    for candidate in _default_csegraph_candidates(repo_path):
        if candidate.is_file():
            return str(candidate.resolve())

    which_result = shutil.which("csegraph")
    if which_result:
        return str(Path(which_result).resolve())

    raise McpLauncherResolutionError(
        "Could not find a real csegraph CLI executable. "
        "Install CseGraph or rerun with --command /absolute/path/to/csegraph. "
        "Generated MCP configs never fall back to python -c or private module entrypoints."
    )


def build_mcp_server_entry(
    repo: str | Path,
    *,
    command: str = "csegraph",
    vscode_style: bool = False,
    include_cwd: bool = False,
    platform: str | None = None,
) -> dict[str, Any]:
    repo_path = Path(repo).resolve()
    resolved_command = resolve_csegraph_executable(repo_path, command=command)
    args = ["serve", "--repo", str(repo_path)]
    if platform:
        args.extend(["--platform", platform])

    entry: dict[str, Any] = {"command": resolved_command, "args": args}
    if include_cwd:
        entry["cwd"] = str(repo_path)
    if vscode_style:
        entry = {"type": "stdio", **entry}
    else:
        entry["type"] = "stdio"
    return entry


def _looks_like_path(command: str) -> bool:
    return "/" in command or "\\" in command or command.startswith(".") or command.startswith("~")


def _path_command_candidates(repo: Path, command: str) -> list[Path]:
    path = Path(command).expanduser()
    if path.is_absolute():
        return [path]
    return [repo / path, Path.cwd() / path]


def _default_csegraph_candidates(repo: Path) -> list[Path]:
    candidates: list[Path] = []
    running = Path(sys.argv[0]).expanduser()
    if running.name in _script_names():
        candidates.append(running if running.is_absolute() else Path.cwd() / running)
    bindir = "Scripts" if sys.platform.startswith("win") else "bin"
    for base in (
        repo / "env" / bindir,
        repo / ".venv" / bindir,
        repo / "venv" / bindir,
        repo / ".env" / bindir,
        Path(sys.prefix) / bindir,
        *_user_script_dirs(),
    ):
        candidates.extend(base / name for name in _script_names())
    return candidates


def _user_script_dirs() -> list[Path]:
    schemes: list[str] = []
    try:
        schemes.append(sysconfig.get_preferred_scheme("user"))
    except (AttributeError, KeyError):
        pass
    schemes.append("nt_user" if sys.platform.startswith("win") else "posix_user")
    if sys.platform == "darwin":
        schemes.append("osx_framework_user")

    dirs: list[Path] = []
    for scheme in dict.fromkeys(schemes):
        try:
            scripts = sysconfig.get_path("scripts", scheme=scheme)
        except (KeyError, AttributeError):
            continue
        if scripts:
            dirs.append(Path(scripts).expanduser())
    return dirs


def _first_existing_script(path: Path) -> Path | None:
    if path.is_file():
        return path
    if path.suffix:
        return None
    for suffix in _script_suffixes():
        candidate = path.with_name(path.name + suffix)
        if candidate.is_file():
            return candidate
    return None


def _script_names() -> tuple[str, ...]:
    return tuple("csegraph" + suffix for suffix in _script_suffixes())


def _script_suffixes() -> tuple[str, ...]:
    if sys.platform.startswith("win"):
        return (".exe", ".cmd", ".bat", "")
    return ("",)
