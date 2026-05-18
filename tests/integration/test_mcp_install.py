from __future__ import annotations

import json
from pathlib import Path

from csegraph_core.mcp_install import McpInstallService


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_cursor_install_merges_without_overwriting_unrelated_servers(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    config = repo / ".cursor" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "existing": {
                        "command": "node",
                        "args": ["server.js"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = McpInstallService(repo, command="custom-csegraph").install(
        platform="cursor",
        dry_run=False,
    )

    data = _read_json(config)
    assert data["mcpServers"]["existing"]["command"] == "node"
    assert data["mcpServers"]["csegraph"] == {
        "command": "custom-csegraph",
        "args": ["serve"],
    }
    assert result.installed[0].platform == "cursor"
    assert result.installed[0].action == "updated"


def test_copilot_install_uses_vscode_servers_key(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    config = repo / ".vscode" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"servers": {"other": {"type": "http", "url": "https://example.test"}}}), encoding="utf-8")

    McpInstallService(repo).install(platform="copilot", dry_run=False)

    data = _read_json(config)
    assert "mcpServers" not in data
    assert data["servers"]["other"]["type"] == "http"
    assert data["servers"]["csegraph"] == {
        "type": "stdio",
        "command": "csegraph",
        "args": ["serve"],
    }


def test_auto_install_writes_root_mcp_and_skips_missing_platform_configs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = McpInstallService(repo).install(platform="auto", dry_run=False)

    data = _read_json(repo / ".mcp.json")
    assert data["mcpServers"]["csegraph"] == {
        "command": "csegraph",
        "args": ["serve"],
    }
    skipped = {target.platform for target in result.skipped}
    assert {"cursor", "gemini-cli", "kiro", "copilot"} <= skipped
    assert {target.platform for target in result.installed} == {"claude-code"}


def test_dry_run_does_not_write_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = McpInstallService(repo).install(platform="cursor", dry_run=True)

    assert not (repo / ".cursor" / "mcp.json").exists()
    assert result.installed[0].dry_run is True
    assert result.installed[0].path.endswith(".cursor/mcp.json")


def test_codex_install_preserves_unrelated_toml_config(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    codex_config = home / ".codex" / "config.toml"
    codex_config.parent.mkdir(parents=True)
    codex_config.write_text(
        '[mcp_servers.existing]\ncommand = "node"\nargs = ["server.js"]\n\n[profiles.dev]\nmodel = "gpt-5.4"\n',
        encoding="utf-8",
    )

    result = McpInstallService(repo, home=home).install(platform="codex", dry_run=False)

    text = codex_config.read_text(encoding="utf-8")
    assert "[mcp_servers.existing]" in text
    assert "[profiles.dev]" in text
    assert "[mcp_servers.csegraph]" in text
    assert 'command = "csegraph"' in text
    assert 'args = ["serve"]' in text
    assert result.installed[0].platform == "codex"

