from __future__ import annotations

import json
import os
import sys
from pathlib import Path, PureWindowsPath

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

import csegraph._core.mcp_install as mcp_install
from csegraph._core.mcp_doctor import McpDoctorService
from csegraph._core.mcp_install import McpInstallService


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_fake_cli(repo: Path) -> str:
    """Create the console-script layout used by the current test host."""
    cli = (
        repo / "env" / "Scripts" / "csegraph.exe"
        if sys.platform.startswith("win")
        else repo / "env" / "bin" / "csegraph"
    )
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text("" if sys.platform.startswith("win") else "#!/bin/sh\n", encoding="utf-8")
    if not sys.platform.startswith("win"):
        os.chmod(cli, 0o755)
    return str(cli.resolve())


def _write_fake_windows_cli(repo: Path) -> str:
    cli = repo / "env" / "Scripts" / "csegraph.exe"
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text("", encoding="utf-8")
    return str(cli.resolve())


def _serve_args(repo: Path, platform: str) -> list[str]:
    return ["serve", "--repo", str(repo.resolve()), "--platform", platform]


def _mcp_entry(repo: Path, platform: str) -> dict:
    if platform == "codex":
        data = tomllib.loads((repo / ".codex" / "config.toml").read_text(encoding="utf-8"))
        return data["mcp_servers"]["csegraph"]
    if platform == "copilot":
        return _read_json(repo / ".vscode" / "mcp.json")["servers"]["csegraph"]
    if platform == "claude-code":
        return _read_json(repo / ".mcp.json")["mcpServers"]["csegraph"]
    return _read_json(repo / ".cursor" / "mcp.json")["mcpServers"]["csegraph"]


def test_gitignore_entries_normalize_windows_paths(monkeypatch) -> None:
    monkeypatch.setitem(
        mcp_install._HOOK_CONFIGS["codex"],
        "path",
        PureWindowsPath(".codex") / "hooks.json",
    )

    entries = mcp_install._gitignore_entries("codex", instructions=False, hooks=True)

    assert ".codex/hooks.json" in entries


def test_cursor_install_merges_without_overwriting_unrelated_servers(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    command = _write_fake_cli(repo)
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

    result = McpInstallService(repo, command=command).install(
        platform="cursor",
        dry_run=False,
    )

    data = _read_json(config)
    assert data["mcpServers"]["existing"]["command"] == "node"
    assert data["mcpServers"]["csegraph"] == {
        "type": "stdio",
        "command": command,
        "args": _serve_args(repo, "cursor"),
    }
    assert result.installed[0].platform == "cursor"
    assert result.installed[0].action == "updated"
    assert "Run `csegraph install --platform cursor`" in result.next_steps[0]
    assert "Open each configured client" in result.next_steps[1]
    assert "Confirm the six CseGraph tools" in result.next_steps[2]
    assert "--platform cursor --json" in result.next_steps[3]


@pytest.mark.parametrize("host_os", ["darwin", "linux", "win32"])
@pytest.mark.parametrize("platform", ["codex", "cursor", "claude-code", "copilot"])
def test_clean_install_smoke_matrix_generates_native_platform_entry(
    tmp_path: Path,
    monkeypatch,
    host_os: str,
    platform: str,
) -> None:
    monkeypatch.setattr(sys, "platform", host_os)
    repo = tmp_path / f"repo-{host_os}-{platform}"
    command = _write_fake_windows_cli(repo) if host_os == "win32" else _write_fake_cli(repo)

    McpInstallService(repo).install(platform=platform, dry_run=False)

    entry = _mcp_entry(repo, platform)
    assert entry["command"] == command
    assert entry["args"] == _serve_args(repo, platform)
    if platform != "codex":
        assert entry["type"] == "stdio"


def test_cursor_install_resolves_windows_venv_executable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    repo = tmp_path / "repo"
    command = _write_fake_windows_cli(repo)

    McpInstallService(repo).install(platform="cursor", dry_run=False)

    data = _read_json(repo / ".cursor" / "mcp.json")
    assert data["mcpServers"]["csegraph"] == {
        "type": "stdio",
        "command": command,
        "args": _serve_args(repo, "cursor"),
    }


def test_codex_hooks_use_windows_safe_launcher(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    repo = tmp_path / "repo"
    _write_fake_windows_cli(repo)

    McpInstallService(repo).install(platform="codex", dry_run=False)

    hooks = _read_json(repo / ".codex" / "hooks.json")["hooks"]
    refresh = hooks["Stop"][0]["hooks"][0]["command"]
    assert refresh.startswith("cmd /D /C ")
    assert "csegraph.exe" in refresh
    assert " refresh " in refresh
    assert str(repo.resolve()) in refresh
    assert "--profile" not in refresh
    assert "--changed-from-git" not in refresh
    assert "git rev-parse" not in refresh
    assert "PostToolUse" not in hooks
    assert "PreToolUse" not in hooks


def test_copilot_install_uses_vscode_servers_key(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    command = _write_fake_cli(repo)
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
        "command": command,
        "args": _serve_args(repo, "copilot"),
    }


def test_auto_install_writes_repo_local_configs_for_all_supported_clients(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    command = _write_fake_cli(repo)

    result = McpInstallService(repo).install(platform="auto", dry_run=False)

    codex_text = (repo / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.csegraph]" in codex_text
    assert _mcp_entry(repo, "codex") == {
        "command": command,
        "args": _serve_args(repo, "codex"),
    }

    claude = _read_json(repo / ".mcp.json")["mcpServers"]["csegraph"]
    assert claude == {
        "type": "stdio",
        "command": command,
        "args": _serve_args(repo, "claude-code"),
    }
    assert _read_json(repo / ".cursor" / "mcp.json")["mcpServers"]["csegraph"] == {
        "type": "stdio",
        "command": command,
        "args": _serve_args(repo, "cursor"),
    }
    assert _read_json(repo / ".gemini" / "settings.json")["mcpServers"]["csegraph"] == {
        "type": "stdio",
        "command": command,
        "args": _serve_args(repo, "gemini-cli"),
    }
    assert _read_json(repo / ".kiro" / "settings" / "mcp.json")["mcpServers"]["csegraph"] == {
        "type": "stdio",
        "command": command,
        "args": _serve_args(repo, "kiro"),
    }
    antigravity = _read_json(repo / ".agents" / "mcp_config.json")["mcpServers"]["csegraph"]
    assert antigravity["command"] == command
    assert antigravity["args"] == _serve_args(repo, "antigravity-cli")
    assert antigravity["cwd"] == str(repo.resolve())
    assert _read_json(repo / ".vscode" / "mcp.json")["servers"]["csegraph"] == {
        "type": "stdio",
        "command": command,
        "args": _serve_args(repo, "copilot"),
    }
    codex_hooks = _read_json(repo / ".codex" / "hooks.json")["hooks"]
    claude_hooks = _read_json(repo / ".claude" / "settings.json")["hooks"]
    assert set(codex_hooks) == {"Stop"}
    assert set(claude_hooks) == {"Stop"}
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
    assert ".agents/mcp_config.json" in gitignore
    assert ".vscode/mcp.json" in gitignore
    assert ".claude/settings.json" in gitignore
    assert ".csegraphinclude" in gitignore
    assert "AGENTS.md" in gitignore
    assert result.skipped == []
    assert "Run `csegraph install --platform auto`" in result.next_steps[0]
    assert "Open each configured client" in result.next_steps[1]
    assert "Confirm the six CseGraph tools" in result.next_steps[2]
    assert "--platform auto --json" in result.next_steps[3]
    assert {target.platform for target in result.installed} == {
        "codex",
        "claude-code",
        "cursor",
        "gemini-cli",
        "kiro",
        "antigravity-cli",
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


def test_antigravity_ide_is_global_explicit_opt_in(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    command = _write_fake_cli(repo)
    home = tmp_path / "home"

    result = McpInstallService(repo, home=home).install(
        platform="antigravity-ide",
        dry_run=False,
    )

    config = home / ".gemini" / "config" / "mcp_config.json"
    entry = _read_json(config)["mcpServers"]["csegraph"]
    assert entry["command"] == command
    assert entry["args"] == _serve_args(repo, "antigravity-ide")
    assert entry["cwd"] == str(repo.resolve())
    assert result.installed[0].scope == "global"
    assert not (repo / ".gemini" / "config" / "mcp_config.json").exists()


def test_doctor_reports_written_config(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_cli(repo)
    McpInstallService(repo).install(platform="cursor", dry_run=False)

    result = McpDoctorService(repo).doctor(
        platform="cursor",
        verify=False,
    )

    assert result.config_present is True
    assert result.launcher_present is True
    assert result.state == "config_written"


def test_doctor_reports_codex_missing_when_only_cursor_is_installed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_cli(repo)
    McpInstallService(repo).install(platform="cursor", dry_run=False)

    result = McpDoctorService(repo).doctor(
        platform="codex",
        verify=False,
    )

    assert result.state == "config_missing"
    assert result.config_present is False
    assert result.recommendations == ["Run `csegraph install --platform codex`."]


def test_doctor_auto_reports_project_scoped_platforms(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_cli(repo)
    McpInstallService(repo).install(platform="auto", dry_run=False)

    result = McpDoctorService(repo).doctor_all(verify=False)

    assert result.platform == "auto"
    assert result.state == "config_written"
    assert result.configured_count == 7
    assert result.missing_count == 1
    assert result.launcher_missing_count == 0
    assert result.contract_invalid_count == 0
    assert result.protocol_verified_count == 0
    platforms = {platform.platform: platform for platform in result.platforms}
    assert set(platforms) == {
        "codex",
        "claude-code",
        "cursor",
        "gemini-cli",
        "kiro",
        "antigravity-cli",
        "copilot",
        "vscode",
    }
    assert "antigravity-ide" not in platforms
    assert platforms["vscode"].state == "config_missing"
    assert platforms["codex"].launcher_present is True


def test_doctor_auto_surfaces_launcher_missing_for_configured_platform(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = repo / ".cursor" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "csegraph": {
                        "type": "stdio",
                        "command": str(repo / "missing-csegraph"),
                        "args": _serve_args(repo, "cursor"),
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = McpDoctorService(repo).doctor_all(verify=False)

    assert result.state == "launcher_missing"
    assert result.configured_count == 1
    assert result.launcher_missing_count == 1
    platforms = {platform.platform: platform for platform in result.platforms}
    assert platforms["cursor"].state == "launcher_missing"


def test_doctor_flags_stale_config_that_only_happens_to_launch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_cli(repo)
    config = repo / ".gemini" / "settings.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "csegraph": {
                        "type": "stdio",
                        "command": "env/bin/csegraph",
                        "args": ["serve"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = McpDoctorService(repo).doctor(platform="gemini-cli", verify=False)

    assert result.config_present is True
    assert result.launcher_present is False
    assert result.contract_valid is False
    assert result.state == "launcher_missing"
    assert "absolute csegraph executable path" in result.contract_issues[0]
    assert any("serve --repo" in recommendation for recommendation in result.recommendations)


def test_codex_install_preserves_unrelated_toml_config(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    command = _write_fake_cli(repo)
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
    assert _mcp_entry(repo, "codex") == {
        "command": command,
        "args": _serve_args(repo, "codex"),
    }
    assert result.installed[0].platform == "codex"
    assert result.installed[0].scope == "project"
    assert not (home / ".codex" / "config.toml").exists()
    hooks = _read_json(repo / ".codex" / "hooks.json")["hooks"]
    assert set(hooks) == {"Stop"}
    assert (repo / "AGENTS.md").exists()
    assert (repo / "CODEX.md").exists()
    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    assert ".codex/config.toml" in gitignore
    assert ".codex/hooks.json" in gitignore
    assert ".csegraphinclude" in gitignore
    assert "AGENTS.md" in gitignore
    assert "CODEX.md" in gitignore


def test_codex_install_merges_existing_hooks_json(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    command_path = _write_fake_cli(repo)
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

    McpInstallService(repo, command=command_path).install(
        platform="codex", hooks=True, dry_run=False
    )
    McpInstallService(repo, command=command_path).install(
        platform="codex", hooks=True, dry_run=False
    )

    hooks = _read_json(hooks_path)["hooks"]
    assert hooks["Stop"][0]["hooks"][0]["command"] == "echo done"
    assert any(group.get("matcher") == "Bash" for group in hooks["PostToolUse"])
    csegraph_refresh_groups = [
        group
        for group in hooks["Stop"]
        if group.get("hooks", [{}])[0].get("statusMessage")
        == "Refreshing CseGraph index after the agent turn"
    ]
    assert len(csegraph_refresh_groups) == 1
    command = csegraph_refresh_groups[0]["hooks"][0]["command"]
    assert "git rev-parse" not in command
    assert f"{command_path} refresh {repo.resolve()}" in command
    assert "--profile" not in command
    assert "--changed-from-git" not in command


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
        assert "MCP/tools UI" in content
        assert "another platform's MCP config is not enough" in content
        assert "do not query `.csegraph/index.db` directly" in content
        assert "do not\n  use CLI context commands as a substitute" in content


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
    assert set(settings["hooks"]) == {"Stop"}


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
    command = _write_fake_cli(repo)

    result = McpInstallService(repo, command="csegraph").install(
        platform="vscode",
        dry_run=False,
    )

    vscode_targets = [t for t in result.installed if t.platform == "vscode"]
    assert len(vscode_targets) == 3

    settings = _read_json(repo / ".vscode" / "settings.json")
    assert settings["csegraph.command"] == command
    assert settings["csegraph.autoRefresh"] is True
    assert settings["csegraph.statusBar"] is True

    tasks = _read_json(repo / ".vscode" / "tasks.json")
    assert tasks["version"] == "2.0.1"
    by_label = {t["label"]: t for t in tasks["tasks"]}
    labels = set(by_label)
    assert labels == {"csegraph: Build Index", "csegraph: Refresh", "csegraph: Status"}
    assert {t["type"] for t in tasks["tasks"]} == {"process"}
    assert {t["command"] for t in tasks["tasks"]} == {command}
    assert by_label["csegraph: Build Index"]["args"] == ["index"]
    assert by_label["csegraph: Refresh"]["args"] == ["refresh"]
    assert by_label["csegraph: Status"]["args"] == ["status", "--verbose"]

    extensions = _read_json(repo / ".vscode" / "extensions.json")
    assert "rishiishah.csegraph-vscode" in extensions["recommendations"]


def test_vscode_install_merges_with_existing_settings(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    command = _write_fake_cli(repo)
    settings_path = repo / ".vscode" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"editor.fontSize": 14, "python.linting.enabled": True}),
        encoding="utf-8",
    )

    McpInstallService(repo, command=command).install(
        platform="vscode",
        dry_run=False,
    )

    data = _read_json(settings_path)
    assert data["editor.fontSize"] == 14
    assert data["python.linting.enabled"] is True
    assert data["csegraph.command"] == command
    assert data["csegraph.autoRefresh"] is True


def test_vscode_install_merges_tasks_without_duplicating(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_fake_cli(repo)
    tasks_path = repo / ".vscode" / "tasks.json"
    tasks_path.parent.mkdir(parents=True)
    tasks_path.write_text(
        json.dumps(
            {
                "version": "2.0.1",
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
