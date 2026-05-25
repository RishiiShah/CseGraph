from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from csegraph_core.core.models import McpInstallResult, McpInstallTarget

Platform = Literal[
    "auto",
    "codex",
    "cursor",
    "claude-code",
    "gemini-cli",
    "kiro",
    "copilot",
    "vscode",
]

_PROJECT_JSON_TARGETS = {
    "claude-code": (Path(".mcp.json"), "mcpServers", False),
    "cursor": (Path(".cursor") / "mcp.json", "mcpServers", False),
    "gemini-cli": (Path(".gemini") / "settings.json", "mcpServers", False),
    "kiro": (Path(".kiro") / "settings" / "mcp.json", "mcpServers", False),
    "copilot": (Path(".vscode") / "mcp.json", "servers", True),
}

_PLATFORMS = {"auto", "codex", "vscode", *_PROJECT_JSON_TARGETS}

_INSTRUCTION_BODY = """\
# CseGraph — Agent Instructions

CseGraph is a context engine: deliver the smallest accurate code slice for the
task, not whole-repo reads or broad grep. This repository has a csegraph index.
Use csegraph MCP tools for code context instead of broad file reads or grep scans.

## Workflow

1. Call `csegraph_minimal` first to get a routing card with key entities and
   next-tool suggestions.
2. Follow the `next_tool_suggestions` — call exactly one suggested tool.
3. Use `csegraph_context` with `detail_level=auto` for task-specific context.
4. Escalate to `csegraph_graph` or `csegraph_path` only for structural
   dependency questions.
5. Never make more than 3 csegraph tool calls in a single turn.
6. If the routing card warns about a stale index, call `csegraph_refresh` first.
"""

_INSTRUCTION_FILES = {
    "CLAUDE.md": _INSTRUCTION_BODY,
    "AGENTS.md": _INSTRUCTION_BODY,
    "GEMINI.md": _INSTRUCTION_BODY,
    "CODEX.md": _INSTRUCTION_BODY,
}

_HOOK_CONFIGS: dict[str, dict[str, Any]] = {
    "claude-code": {
        "path": Path(".claude") / "settings.json",
        "build": lambda cmd: {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Edit|Write",
                        "hooks": [{"type": "command", "command": f"{cmd} refresh . --profile small 2>$null"}],
                    }
                ],
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": f"{cmd} status 2>$null || true"}],
                    }
                ],
            }
        },
    },
}


_VSCODE_TASKS = [
    {
        "label": "csegraph: Build Index",
        "type": "shell",
        "command": "csegraph index",
        "group": "build",
        "problemMatcher": [],
    },
    {
        "label": "csegraph: Refresh",
        "type": "shell",
        "command": "csegraph refresh",
        "problemMatcher": [],
    },
    {
        "label": "csegraph: Status",
        "type": "shell",
        "command": "csegraph status --verbose",
        "problemMatcher": [],
    },
]


class McpInstallService:
    def __init__(
        self,
        repo: str | Path,
        *,
        command: str = "csegraph",
        home: str | Path | None = None,
    ) -> None:
        self.repo = Path(repo).resolve()
        self.command = command
        self.home = Path(home).resolve() if home is not None else Path.home()

    def install(
        self,
        *,
        platform: Platform = "auto",
        dry_run: bool = False,
        instructions: bool = False,
        hooks: bool = False,
    ) -> McpInstallResult:
        if platform not in _PLATFORMS:
            raise ValueError(f"Unsupported MCP install platform: {platform}")

        result = McpInstallResult(
            command="install",
            repo_root=str(self.repo),
            server_name="csegraph",
            server_command=self.command,
            server_args=["serve"],
            dry_run=dry_run,
        )

        if platform == "auto":
            self._install_project_json("claude-code", dry_run, result, force=True)
            for candidate in ("cursor", "gemini-cli", "kiro", "copilot"):
                self._install_project_json(candidate, dry_run, result, force=False)
        elif platform == "codex":
            self._install_codex(dry_run, result)
        elif platform == "vscode":
            self._install_vscode(dry_run, result)
        else:
            self._install_project_json(platform, dry_run, result, force=True)

        if instructions:
            self._install_instructions(dry_run, result)
        if hooks:
            self._install_agent_hooks(dry_run, result)

        return result

    def _install_project_json(
        self,
        platform: str,
        dry_run: bool,
        result: McpInstallResult,
        *,
        force: bool,
    ) -> None:
        rel_path, section, vscode_style = _PROJECT_JSON_TARGETS[platform]
        path = self.repo / rel_path
        if not force and not path.exists() and not path.parent.exists():
            result.skipped.append(
                McpInstallTarget(
                    platform=platform,
                    path=str(path),
                    scope="project",
                    action="skipped",
                    dry_run=dry_run,
                    reason="platform config not present",
                )
            )
            return

        action = "updated" if path.exists() else "created"
        if not dry_run:
            data = _read_json_object(path)
            servers = data.setdefault(section, {})
            servers["csegraph"] = self._server_entry(vscode_style=vscode_style)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        result.installed.append(
            McpInstallTarget(
                platform=platform,
                path=str(path),
                scope="project",
                action=action,
                dry_run=dry_run,
            )
        )

    def _install_codex(self, dry_run: bool, result: McpInstallResult) -> None:
        path = self.home / ".codex" / "config.toml"
        action = "updated" if path.exists() else "created"

        if not dry_run:
            try:
                import tomlkit
            except ImportError as exc:  # pragma: no cover - exercised when packaging is broken
                raise RuntimeError(
                    "Codex MCP install requires tomlkit. Install csegraph-core with its dependencies."
                ) from exc

            doc = tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()
            servers = doc.setdefault("mcp_servers", tomlkit.table())
            table = tomlkit.table()
            table["command"] = self.command
            table["args"] = ["serve"]
            servers["csegraph"] = table
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(tomlkit.dumps(doc), encoding="utf-8")

        result.installed.append(
            McpInstallTarget(
                platform="codex",
                path=str(path),
                scope="user",
                action=action,
                dry_run=dry_run,
            )
        )

    def _install_vscode(self, dry_run: bool, result: McpInstallResult) -> None:
        settings_path = self.repo / ".vscode" / "settings.json"
        action = "updated" if settings_path.exists() else "created"
        if not dry_run:
            data = _read_json_object(settings_path)
            data["csegraph.command"] = self.command
            data["csegraph.autoRefresh"] = True
            data["csegraph.statusBar"] = True
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(
                json.dumps(data, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        result.installed.append(
            McpInstallTarget(
                platform="vscode",
                path=str(settings_path),
                scope="project",
                action=action,
                dry_run=dry_run,
            )
        )

        tasks_path = self.repo / ".vscode" / "tasks.json"
        tasks_action = "updated" if tasks_path.exists() else "created"
        if not dry_run:
            tasks_data = _read_json_object(tasks_path)
            tasks_data.setdefault("version", "1.7.0")
            tasks_list = tasks_data.setdefault("tasks", [])
            existing_labels = {t.get("label") for t in tasks_list if isinstance(t, dict)}
            for task in _VSCODE_TASKS:
                if task["label"] not in existing_labels:
                    tasks_list.append(task)
            tasks_path.write_text(
                json.dumps(tasks_data, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        result.installed.append(
            McpInstallTarget(
                platform="vscode",
                path=str(tasks_path),
                scope="project",
                action=tasks_action,
                dry_run=dry_run,
            )
        )

        recs_path = self.repo / ".vscode" / "extensions.json"
        recs_action = "updated" if recs_path.exists() else "created"
        if not dry_run:
            recs_data = _read_json_object(recs_path)
            recs = recs_data.setdefault("recommendations", [])
            if "csegraph.csegraph-vscode" not in recs:
                recs.append("csegraph.csegraph-vscode")
            recs_path.write_text(
                json.dumps(recs_data, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        result.installed.append(
            McpInstallTarget(
                platform="vscode",
                path=str(recs_path),
                scope="project",
                action=recs_action,
                dry_run=dry_run,
            )
        )

    def _install_instructions(self, dry_run: bool, result: McpInstallResult) -> None:
        for filename, body in _INSTRUCTION_FILES.items():
            path = self.repo / filename
            if path.exists():
                existing = path.read_text(encoding="utf-8")
                if "csegraph" in existing.lower():
                    result.skipped.append(
                        McpInstallTarget(
                            platform="instructions",
                            path=str(path),
                            scope="project",
                            action="skipped",
                            dry_run=dry_run,
                            reason="already contains csegraph guidance",
                        )
                    )
                    continue
                action = "updated"
                new_content = existing.rstrip() + "\n\n" + body
            else:
                action = "created"
                new_content = body

            if not dry_run:
                path.write_text(new_content, encoding="utf-8")

            result.installed.append(
                McpInstallTarget(
                    platform="instructions",
                    path=str(path),
                    scope="project",
                    action=action,
                    dry_run=dry_run,
                )
            )

    def _install_agent_hooks(self, dry_run: bool, result: McpInstallResult) -> None:
        for platform_name, cfg in _HOOK_CONFIGS.items():
            path = self.repo / cfg["path"]
            action = "updated" if path.exists() else "created"

            if not dry_run:
                data = _read_json_object(path)
                hook_data = cfg["build"](self.command)
                for key, value in hook_data.items():
                    data[key] = value
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            result.installed.append(
                McpInstallTarget(
                    platform=f"hooks:{platform_name}",
                    path=str(path),
                    scope="project",
                    action=action,
                    dry_run=dry_run,
                )
            )

    def _server_entry(self, *, vscode_style: bool) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "command": self.command,
            "args": ["serve"],
        }
        if vscode_style:
            entry = {"type": "stdio", **entry}
        return entry


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"MCP config must be a JSON object: {path}")
    return data

