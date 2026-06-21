from __future__ import annotations

import json
import os
from pathlib import Path

from csegraph._core.mcp_install import McpInstallService


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
        "type": "stdio",
        "command": "custom-csegraph",
        "args": ["serve"],
    }
    assert result.installed[0].platform == "cursor"
    assert result.installed[0].action == "updated"


def test_copilot_install_uses_vscode_servers_key(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    config = repo / ".vscode" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"servers": {"other": {"type": "http", "url": "https://example.test"}}}),
        encoding="utf-8",
    )

    McpInstallService(repo).install(platform="copilot", dry_run=False)

    data = _read_json(config)
    assert "mcpServers" not in data
    assert data["servers"]["other"]["type"] == "http"
    assert data["servers"]["csegraph"] == {
        "type": "stdio",
        "command": "csegraph",
        "args": ["serve"],
    }


def test_auto_install_writes_repo_local_configs_for_all_supported_clients(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = McpInstallService(repo).install(platform="auto", dry_run=False)

    codex_text = (repo / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.csegraph]" in codex_text
    assert 'command = "csegraph"' in codex_text
    assert 'args = ["serve"]' in codex_text

    data = _read_json(repo / ".mcp.json")
    assert data["mcpServers"]["csegraph"] == {
        "type": "stdio",
        "command": "csegraph",
        "args": ["serve"],
    }
    assert (
        _read_json(repo / ".cursor" / "mcp.json")["mcpServers"]["csegraph"]["command"] == "csegraph"
    )
    assert (
        _read_json(repo / ".gemini" / "settings.json")["mcpServers"]["csegraph"]["command"]
        == "csegraph"
    )
    assert (
        _read_json(repo / ".kiro" / "settings" / "mcp.json")["mcpServers"]["csegraph"]["command"]
        == "csegraph"
    )
    assert _read_json(repo / ".vscode" / "mcp.json")["servers"]["csegraph"]["command"] == "csegraph"
    codex_hooks = _read_json(repo / ".codex" / "hooks.json")
    assert "PostToolUse" in codex_hooks["hooks"]
    assert "PreToolUse" in codex_hooks["hooks"]
    assert "hooks" in _read_json(repo / ".claude" / "settings.json")
    assert {
        path.name
        for path in repo.glob("*.md")
        if "csegraph" in path.read_text(encoding="utf-8").lower()
    } >= {
        "AGENTS.md",
        "CLAUDE.md",
        "CODEX.md",
        "GEMINI.md",
    }
    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    assert ".csegraph/" in gitignore
    assert ".codex/config.toml" in gitignore
    assert ".codex/hooks.json" in gitignore
    assert ".mcp.json" in gitignore
    assert ".cursor/mcp.json" in gitignore
    assert ".gemini/settings.json" in gitignore
    assert ".kiro/settings/mcp.json" in gitignore
    assert ".vscode/mcp.json" in gitignore
    assert ".claude/settings.json" in gitignore
    assert "AGENTS.md" in gitignore
    assert result.skipped == []
    assert {target.platform for target in result.installed} == {
        "codex",
        "claude-code",
        "cursor",
        "gemini-cli",
        "kiro",
        "copilot",
        "instructions",
        "hooks:claude-code",
        "hooks:codex",
        "gitignore",
    }


def test_dry_run_does_not_write_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = McpInstallService(repo).install(platform="cursor", dry_run=True)

    assert not (repo / ".cursor" / "mcp.json").exists()
    assert not (repo / "AGENTS.md").exists()
    assert not (repo / ".gitignore").exists()
    assert result.installed[0].dry_run is True
    assert result.installed[0].path.endswith(os.path.join(".cursor", "mcp.json"))


def test_codex_install_preserves_unrelated_toml_config(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    codex_config = repo / ".codex" / "config.toml"
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
    assert result.installed[0].scope == "project"
    assert not (home / ".codex" / "config.toml").exists()
    hooks = _read_json(repo / ".codex" / "hooks.json")
    assert (
        hooks["hooks"]["PostToolUse"][0]["hooks"][0]["statusMessage"] == "Refreshing CseGraph index"
    )
    assert hooks["hooks"]["PreToolUse"][0]["hooks"][0]["statusMessage"] == "Checking CseGraph index"
    assert (repo / "AGENTS.md").exists()
    assert (repo / "CODEX.md").exists()
    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    assert ".codex/config.toml" in gitignore
    assert ".codex/hooks.json" in gitignore
    assert "AGENTS.md" in gitignore
    assert "CODEX.md" in gitignore


def test_codex_install_merges_existing_hooks_json(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    hooks_path = repo / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "echo done"}]}],
                    "PostToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "echo bash"}],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    McpInstallService(repo, command="env/bin/csegraph").install(platform="codex", dry_run=False)
    McpInstallService(repo, command="env/bin/csegraph").install(platform="codex", dry_run=False)

    hooks = _read_json(hooks_path)["hooks"]
    assert hooks["Stop"][0]["hooks"][0]["command"] == "echo done"
    assert any(group.get("matcher") == "Bash" for group in hooks["PostToolUse"])
    csegraph_refresh_groups = [
        group
        for group in hooks["PostToolUse"]
        if group.get("hooks", [{}])[0].get("statusMessage") == "Refreshing CseGraph index"
    ]
    assert len(csegraph_refresh_groups) == 1
    command = csegraph_refresh_groups[0]["hooks"][0]["command"]
    assert 'cd "$(git rev-parse --show-toplevel)"' in command
    assert "env/bin/csegraph refresh . --profile small" in command


def test_install_updates_gitignore_without_duplicating_covered_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".codex/\nAGENTS.md\n", encoding="utf-8")

    result = McpInstallService(repo).install(platform="codex", dry_run=False)

    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    assert gitignore.count(".codex/") == 1
    assert ".codex/config.toml" not in gitignore
    assert ".codex/hooks.json" not in gitignore
    assert gitignore.count("AGENTS.md") == 1
    assert "CODEX.md" in gitignore
    assert ".csegraph/" in gitignore
    assert any(target.platform == "gitignore" for target in result.installed)


def test_install_can_skip_gitignore(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = McpInstallService(repo).install(platform="codex", gitignore=False, dry_run=False)

    assert not (repo / ".gitignore").exists()
    assert "gitignore" not in {target.platform for target in result.installed}


# --- Instruction files ---


def test_instructions_creates_all_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = McpInstallService(repo).install(platform="cursor", instructions=True, dry_run=False)

    instruction_targets = [t for t in result.installed if t.platform == "instructions"]
    created_names = {Path(t.path).name for t in instruction_targets}
    assert {"CLAUDE.md", "AGENTS.md", "GEMINI.md", "CODEX.md"} == created_names

    for name in ("CLAUDE.md", "AGENTS.md", "GEMINI.md", "CODEX.md"):
        content = (repo / name).read_text(encoding="utf-8")
        assert "csegraph_minimal" in content
        assert "csegraph_context" in content


def test_instructions_skips_if_already_present(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("# My Project\nUse csegraph for context.\n", encoding="utf-8")

    result = McpInstallService(repo).install(platform="cursor", instructions=True, dry_run=False)

    claude_targets = [t for t in result.skipped if Path(t.path).name == "CLAUDE.md"]
    assert len(claude_targets) == 1
    assert claude_targets[0].reason == "already contains csegraph guidance"
    content = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert content == "# My Project\nUse csegraph for context.\n"


def test_instructions_appends_to_existing_without_csegraph(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Agent Rules\nBe helpful.\n", encoding="utf-8")

    result = McpInstallService(repo).install(platform="cursor", instructions=True, dry_run=False)

    agents_targets = [t for t in result.installed if Path(t.path).name == "AGENTS.md"]
    assert len(agents_targets) == 1
    assert agents_targets[0].action == "updated"
    content = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert content.startswith("# Agent Rules\nBe helpful.")
    assert "csegraph_minimal" in content


def test_instructions_dry_run_does_not_write(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = McpInstallService(repo).install(platform="cursor", instructions=True, dry_run=True)

    instruction_targets = [t for t in result.installed if t.platform == "instructions"]
    assert len(instruction_targets) == 4
    assert all(t.dry_run is True for t in instruction_targets)
    for name in ("CLAUDE.md", "AGENTS.md", "GEMINI.md", "CODEX.md"):
        assert not (repo / name).exists()


# --- Agent hooks ---


def test_hooks_creates_claude_settings(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = McpInstallService(repo).install(platform="cursor", hooks=True, dry_run=False)

    hook_targets = [t for t in result.installed if t.platform.startswith("hooks:")]
    assert len(hook_targets) >= 1
    assert hook_targets[0].platform == "hooks:claude-code"

    settings = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "hooks" in settings
    assert "PostToolUse" in settings["hooks"]
    assert "PreToolUse" in settings["hooks"]


def test_hooks_preserves_existing_claude_settings(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    settings_path = repo / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"permissions": {"allow": ["Read"]}}), encoding="utf-8")

    McpInstallService(repo).install(platform="cursor", hooks=True, dry_run=False)

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["permissions"]["allow"] == ["Read"]
    assert "hooks" in settings


def test_hooks_dry_run_does_not_write(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = McpInstallService(repo).install(platform="cursor", hooks=True, dry_run=True)

    hook_targets = [t for t in result.installed if t.platform.startswith("hooks:")]
    assert len(hook_targets) >= 1
    assert all(t.dry_run is True for t in hook_targets)
    assert not (repo / ".claude" / "settings.json").exists()


# --- VS Code platform ---


def test_vscode_install_creates_three_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = McpInstallService(repo, command="csegraph").install(
        platform="vscode",
        dry_run=False,
    )

    vscode_targets = [t for t in result.installed if t.platform == "vscode"]
    assert len(vscode_targets) == 3

    settings = _read_json(repo / ".vscode" / "settings.json")
    assert settings["csegraph.command"] == "csegraph"
    assert settings["csegraph.autoRefresh"] is True
    assert settings["csegraph.statusBar"] is True

    tasks = _read_json(repo / ".vscode" / "tasks.json")
    assert tasks["version"] == "1.8.0"
    labels = {t["label"] for t in tasks["tasks"]}
    assert labels == {"csegraph: Build Index", "csegraph: Refresh", "csegraph: Status"}

    extensions = _read_json(repo / ".vscode" / "extensions.json")
    assert "rishiishah.csegraph-vscode" in extensions["recommendations"]


def test_vscode_install_merges_with_existing_settings(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    settings_path = repo / ".vscode" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"editor.fontSize": 14, "python.linting.enabled": True}),
        encoding="utf-8",
    )

    McpInstallService(repo, command="my-csegraph").install(
        platform="vscode",
        dry_run=False,
    )

    data = _read_json(settings_path)
    assert data["editor.fontSize"] == 14
    assert data["python.linting.enabled"] is True
    assert data["csegraph.command"] == "my-csegraph"
    assert data["csegraph.autoRefresh"] is True


def test_vscode_install_merges_tasks_without_duplicating(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    tasks_path = repo / ".vscode" / "tasks.json"
    tasks_path.parent.mkdir(parents=True)
    tasks_path.write_text(
        json.dumps(
            {
                "version": "1.8.0",
                "tasks": [
                    {"label": "csegraph: Build Index", "type": "shell", "command": "old"},
                    {"label": "my-task", "type": "shell", "command": "echo hi"},
                ],
            }
        ),
        encoding="utf-8",
    )

    McpInstallService(repo).install(platform="vscode", dry_run=False)

    data = _read_json(tasks_path)
    labels = [t["label"] for t in data["tasks"]]
    assert labels.count("csegraph: Build Index") == 1
    assert "my-task" in labels
    assert "csegraph: Refresh" in labels
    assert "csegraph: Status" in labels


def test_vscode_install_does_not_duplicate_extension_recommendation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    ext_path = repo / ".vscode" / "extensions.json"
    ext_path.parent.mkdir(parents=True)
    ext_path.write_text(
        json.dumps({"recommendations": ["rishiishah.csegraph-vscode", "ms-python.python"]}),
        encoding="utf-8",
    )

    McpInstallService(repo).install(platform="vscode", dry_run=False)

    data = _read_json(ext_path)
    assert data["recommendations"].count("rishiishah.csegraph-vscode") == 1
    assert "ms-python.python" in data["recommendations"]


def test_vscode_dry_run_does_not_write_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    result = McpInstallService(repo).install(platform="vscode", dry_run=True)

    vscode_targets = [t for t in result.installed if t.platform == "vscode"]
    assert len(vscode_targets) == 3
    assert all(t.dry_run is True for t in vscode_targets)
    assert not (repo / ".vscode" / "settings.json").exists()
    assert not (repo / ".vscode" / "tasks.json").exists()
    assert not (repo / ".vscode" / "extensions.json").exists()
