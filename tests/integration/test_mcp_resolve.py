from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import csegraph._core.mcp_resolve as mcp_resolve
from csegraph._core.mcp_resolve import McpLauncherResolutionError, build_mcp_server_entry


def test_build_entry_uses_project_venv_cli(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cli = repo / "env" / "bin" / "csegraph"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(cli, 0o755)

    entry = build_mcp_server_entry(repo)

    assert entry == {
        "type": "stdio",
        "command": str(cli.resolve()),
        "args": ["serve", "--repo", str(repo.resolve())],
    }


@pytest.mark.parametrize("venv_dir", ["env", ".venv", "venv", ".env"])
def test_build_entry_resolves_common_project_venv_dirs(
    tmp_path: Path,
    monkeypatch,
    venv_dir: str,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    repo = tmp_path / "repo"
    cli = repo / venv_dir / "bin" / "csegraph"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(cli, 0o755)

    entry = build_mcp_server_entry(repo, platform="codex")

    assert entry == {
        "type": "stdio",
        "command": str(cli.resolve()),
        "args": ["serve", "--repo", str(repo.resolve()), "--platform", "codex"],
    }


def test_build_entry_does_not_fall_back_to_python_module(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    py = repo / "env" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")

    try:
        entry = build_mcp_server_entry(repo)
    except McpLauncherResolutionError:
        return
    assert entry["command"] != str(py.resolve())
    assert "-m" not in entry["args"]


def test_custom_command_is_not_rewritten(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cli = repo / "bin" / "my-csegraph"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(cli, 0o755)

    entry = build_mcp_server_entry(repo, command="bin/my-csegraph")

    assert entry["command"] == str(cli.resolve())
    assert entry["args"] == ["serve", "--repo", str(repo.resolve())]


def test_build_entry_can_include_host_platform(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cli = repo / "env" / "bin" / "csegraph"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(cli, 0o755)

    entry = build_mcp_server_entry(repo, platform="codex")

    assert entry["command"] == str(cli.resolve())
    assert entry["args"] == ["serve", "--repo", str(repo.resolve()), "--platform", "codex"]


def test_windows_project_venv_cli_resolves_exe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    repo = tmp_path / "repo"
    cli = repo / "env" / "Scripts" / "csegraph.exe"
    cli.parent.mkdir(parents=True)
    cli.write_text("", encoding="utf-8")

    entry = build_mcp_server_entry(repo)

    assert entry["command"] == str(cli.resolve())
    assert entry["args"] == ["serve", "--repo", str(repo.resolve())]


def test_windows_explicit_extensionless_path_resolves_exe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    repo = tmp_path / "repo"
    cli = repo / "env" / "Scripts" / "csegraph.exe"
    cli.parent.mkdir(parents=True)
    cli.write_text("", encoding="utf-8")

    entry = build_mcp_server_entry(repo, command="env/Scripts/csegraph")

    assert entry["command"] == str(cli.resolve())
    assert entry["args"] == ["serve", "--repo", str(repo.resolve())]


def test_windows_user_install_scripts_dir_resolves_exe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "python-prefix"))
    user_scripts = tmp_path / "AppData" / "Roaming" / "Python" / "Python314" / "Scripts"
    cli = user_scripts / "csegraph.exe"
    cli.parent.mkdir(parents=True)
    cli.write_text("", encoding="utf-8")

    def fake_get_path(name: str, scheme: str | None = None) -> str:
        assert name == "scripts"
        if scheme == "nt_user":
            return str(user_scripts)
        raise KeyError(scheme)

    monkeypatch.setattr(mcp_resolve.sysconfig, "get_preferred_scheme", lambda _kind: "nt_user")
    monkeypatch.setattr(mcp_resolve.sysconfig, "get_path", fake_get_path)

    entry = build_mcp_server_entry(tmp_path / "repo")

    assert entry["command"] == str(cli.resolve())
    assert entry["args"] == ["serve", "--repo", str((tmp_path / "repo").resolve())]


def test_posix_user_install_bin_dir_resolves_script(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "python-prefix"))
    user_bin = tmp_path / ".local" / "bin"
    cli = user_bin / "csegraph"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(cli, 0o755)

    def fake_get_path(name: str, scheme: str | None = None) -> str:
        assert name == "scripts"
        if scheme == "posix_user":
            return str(user_bin)
        raise KeyError(scheme)

    monkeypatch.setattr(mcp_resolve.sysconfig, "get_preferred_scheme", lambda _kind: "posix_user")
    monkeypatch.setattr(mcp_resolve.sysconfig, "get_path", fake_get_path)

    entry = build_mcp_server_entry(tmp_path / "repo")

    assert entry["command"] == str(cli.resolve())
    assert entry["args"] == ["serve", "--repo", str((tmp_path / "repo").resolve())]
