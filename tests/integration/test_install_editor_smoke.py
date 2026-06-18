"""Smoke tests for MCP install targets and VS Code extension surface."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import run_cli

_ROOT = Path(__file__).resolve().parents[2]
_VSCODE_PKG = _ROOT / "csegraph-vscode" / "package.json"
_VSCODE_SRC = _ROOT / "csegraph-vscode" / "src" / "extension.ts"


def test_install_codex_dry_run_json():
    result = run_cli("install", str(_ROOT), "--platform", "codex", "--dry-run", "--json")
    assert result["command"] == "install"
    platforms = {t["platform"] for t in result["installed"]}
    assert "codex" in platforms


def test_install_cursor_dry_run_json():
    result = run_cli("install", str(_ROOT), "--platform", "cursor", "--dry-run", "--json")
    platforms = {t["platform"] for t in result["installed"]}
    assert "cursor" in platforms


def test_vscode_extension_declares_core_commands():
    data = json.loads(_VSCODE_PKG.read_text(encoding="utf-8"))
    commands = {c["command"] for c in data["contributes"]["commands"]}
    assert {
        "csegraph.index",
        "csegraph.refresh",
        "csegraph.status",
        "csegraph.context",
        "csegraph.inspect",
    } <= commands
    assert data["engines"]["vscode"]


def test_vscode_extension_exposes_cli_command_setting():
    data = json.loads(_VSCODE_PKG.read_text(encoding="utf-8"))
    props = data.get("contributes", {}).get("configuration", {}).get("properties", {})
    assert "csegraph.command" in props


def test_vscode_extension_uses_profile_setting_for_cli_commands():
    source = _VSCODE_SRC.read_text(encoding="utf-8")

    assert 'get<string>("profile", "medium")' in source
    assert '"--profile"' in source
    assert "getProfileArgs" in source


def test_vscode_extension_redacts_command_args_when_output_logging_disabled():
    source = _VSCODE_SRC.read_text(encoding="utf-8")

    assert "formatCommandLine" in source
    assert "arguments hidden" in source
    assert '${cli} ${allArgs.join(" ")}' not in source


def test_vscode_extension_caches_discovered_cli_per_workspace_root():
    source = _VSCODE_SRC.read_text(encoding="utf-8")

    assert "resolvedCliByRoot" in source
    assert "new Map<string, string>()" in source
    assert "getCliCommand(root)" in source
