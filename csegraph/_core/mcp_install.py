from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Literal, Sequence

from csegraph._core.core.models import McpInstallResult, McpInstallTarget
from csegraph._core.mcp_resolve import build_mcp_server_entry

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

_PLATFORM_INSTRUCTION_FILES = {
    "auto": tuple(_INSTRUCTION_FILES),
    "codex": ("AGENTS.md", "CODEX.md"),
    "claude-code": ("CLAUDE.md",),
    "cursor": ("AGENTS.md",),
    "gemini-cli": ("GEMINI.md",),
    "kiro": ("AGENTS.md",),
    "copilot": ("AGENTS.md",),
    "vscode": ("AGENTS.md",),
}


_RUNTIME_GITIGNORE_ENTRIES = (".csegraph/",)


def _csegraph_hook_command(command: str, args: str) -> str:
    executable = shlex.quote(command)
    return f'cd "$(git rev-parse --show-toplevel)" && {executable} {args} >/dev/null 2>&1 || true'


def _claude_hooks(command: str) -> dict[str, Any]:
    return {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Edit|Write",
                    "hooks": [
                        {
                            "type": "command",
                            "command": _csegraph_hook_command(command, "refresh . --profile small"),
                        }
                    ],
                }
            ],
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": _csegraph_hook_command(command, "status ."),
                        }
                    ],
                }
            ],
        }
    }


def _codex_hooks(command: str) -> dict[str, Any]:
    return {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Edit|Write|apply_patch",
                    "hooks": [
                        {
                            "type": "command",
                            "command": _csegraph_hook_command(command, "refresh . --profile small"),
                            "timeout": 120,
                            "statusMessage": "Refreshing CseGraph index",
                        }
                    ],
                }
            ],
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": _csegraph_hook_command(command, "status ."),
                            "timeout": 30,
                            "statusMessage": "Checking CseGraph index",
                        }
                    ],
                }
            ],
        }
    }


_HOOK_CONFIGS: dict[str, dict[str, Any]] = {
    "claude-code": {
        "path": Path(".claude") / "settings.json",
        "build": _claude_hooks,
    },
    "codex": {
        "path": Path(".codex") / "hooks.json",
        "build": _codex_hooks,
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
        instructions: bool | None = None,
        hooks: bool | None = None,
        gitignore: bool | None = None,
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
            self._install_codex(dry_run, result)
            for candidate in ("claude-code", "cursor", "gemini-cli", "kiro", "copilot"):
                self._install_project_json(candidate, dry_run, result, force=True)
        elif platform == "codex":
            self._install_codex(dry_run, result)
        elif platform == "vscode":
            self._install_vscode(dry_run, result)
        else:
            self._install_project_json(platform, dry_run, result, force=True)

        if instructions is not False:
            self._install_instructions(
                dry_run,
                result,
                platform="auto" if instructions is True else platform,
            )
        if hooks is not False:
            self._install_agent_hooks(
                dry_run,
                result,
                platform="auto" if hooks is True else platform,
            )
        if gitignore is not False:
            self._install_gitignore(
                dry_run,
                result,
                platform=platform,
                instructions=instructions,
                hooks=hooks,
            )

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
        path = self.repo / ".codex" / "config.toml"
        action = "updated" if path.exists() else "created"

        if not dry_run:
            try:
                import tomlkit
            except ImportError as exc:  # pragma: no cover - exercised when packaging is broken
                raise RuntimeError(
                    "Codex MCP install requires tomlkit. Install csegraph with its dependencies."
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
                scope="project",
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
            tasks_data.setdefault("version", "1.7.1")
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
            if "rishiishah.csegraph-vscode" not in recs:
                recs.append("rishiishah.csegraph-vscode")
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

    def _install_instructions(
        self,
        dry_run: bool,
        result: McpInstallResult,
        *,
        platform: str,
    ) -> None:
        for filename in _instruction_filenames(platform):
            body = _INSTRUCTION_FILES[filename]
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

    def _install_agent_hooks(
        self,
        dry_run: bool,
        result: McpInstallResult,
        *,
        platform: str,
    ) -> None:
        for platform_name in _hook_platforms(platform):
            cfg = _HOOK_CONFIGS[platform_name]
            path = self.repo / cfg["path"]
            action = "updated" if path.exists() else "created"

            if not dry_run:
                data = _read_json_object(path)
                hook_data = cfg["build"](self.command)
                _merge_hooks(data, hook_data)
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

    def _install_gitignore(
        self,
        dry_run: bool,
        result: McpInstallResult,
        *,
        platform: str,
        instructions: bool | None,
        hooks: bool | None,
    ) -> None:
        path = self.repo / ".gitignore"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        missing = [
            entry
            for entry in _gitignore_entries(platform, instructions=instructions, hooks=hooks)
            if not _gitignore_covers(entry, existing)
        ]

        if not missing:
            result.skipped.append(
                McpInstallTarget(
                    platform="gitignore",
                    path=str(path),
                    scope="project",
                    action="skipped",
                    dry_run=dry_run,
                    reason="already ignores csegraph setup files",
                )
            )
            return

        action = "updated" if path.exists() else "created"
        if not dry_run:
            section = "\n".join(
                ["# CseGraph local setup (regenerate with `csegraph install`)", *missing]
            )
            if existing.strip():
                content = existing.rstrip() + "\n\n" + section + "\n"
            else:
                content = section + "\n"
            path.write_text(content, encoding="utf-8")

        result.installed.append(
            McpInstallTarget(
                platform="gitignore",
                path=str(path),
                scope="project",
                action=action,
                dry_run=dry_run,
            )
        )

    def _server_entry(self, *, vscode_style: bool) -> dict[str, Any]:
        return build_mcp_server_entry(
            self.repo,
            command=self.command,
            vscode_style=vscode_style,
        )


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


def _instruction_filenames(platform: str) -> Sequence[str]:
    return _PLATFORM_INSTRUCTION_FILES.get(platform, ("AGENTS.md",))


def _hook_platforms(platform: str) -> Sequence[str]:
    if platform == "auto":
        return tuple(_HOOK_CONFIGS)
    if platform in _HOOK_CONFIGS:
        return (platform,)
    return ()


def _merge_hooks(data: dict[str, Any], hook_data: dict[str, Any]) -> None:
    incoming_hooks = hook_data.get("hooks")
    if not isinstance(incoming_hooks, dict):
        data.update(hook_data)
        return

    existing_hooks = data.setdefault("hooks", {})
    if not isinstance(existing_hooks, dict):
        data["hooks"] = existing_hooks = {}

    for event_name, incoming_groups in incoming_hooks.items():
        if not isinstance(incoming_groups, list):
            existing_hooks[event_name] = incoming_groups
            continue
        existing_groups = existing_hooks.setdefault(event_name, [])
        if not isinstance(existing_groups, list):
            existing_hooks[event_name] = existing_groups = []
        for incoming_group in incoming_groups:
            _upsert_hook_group(existing_groups, incoming_group)


def _upsert_hook_group(existing_groups: list[Any], incoming_group: Any) -> None:
    incoming_key = _hook_group_key(incoming_group)
    for idx, existing_group in enumerate(existing_groups):
        if _hook_group_key(existing_group) == incoming_key:
            existing_groups[idx] = incoming_group
            return
    existing_groups.append(incoming_group)


def _hook_group_key(group: Any) -> tuple[Any, ...]:
    if not isinstance(group, dict):
        return (id(group),)
    raw_hooks = group.get("hooks")
    hooks = raw_hooks if isinstance(raw_hooks, list) else []
    handler_keys = tuple(_hook_handler_key(handler) for handler in hooks)
    return (group.get("matcher"), handler_keys)


def _hook_handler_key(handler: Any) -> Any:
    if not isinstance(handler, dict):
        return id(handler)
    return handler.get("statusMessage") or handler.get("command")


def _gitignore_entries(
    platform: str,
    *,
    instructions: bool | None,
    hooks: bool | None,
) -> tuple[str, ...]:
    entries: list[str] = list(_RUNTIME_GITIGNORE_ENTRIES)
    entries.extend(_platform_config_gitignore_entries(platform))

    if instructions is not False:
        instruction_platform = "auto" if instructions is True else platform
        entries.extend(_instruction_filenames(instruction_platform))

    if hooks is not False:
        hook_platform = "auto" if hooks is True else platform
        entries.extend(str(_HOOK_CONFIGS[name]["path"]) for name in _hook_platforms(hook_platform))

    return tuple(dict.fromkeys(entries))


def _platform_config_gitignore_entries(platform: str) -> tuple[str, ...]:
    if platform == "auto":
        entries = [".codex/config.toml"]
        entries.extend(str(target[0]) for target in _PROJECT_JSON_TARGETS.values())
        return tuple(entries)
    if platform == "codex":
        return (".codex/config.toml",)
    if platform == "vscode":
        return (
            ".vscode/settings.json",
            ".vscode/tasks.json",
            ".vscode/extensions.json",
        )
    if platform in _PROJECT_JSON_TARGETS:
        return (str(_PROJECT_JSON_TARGETS[platform][0]),)
    return ()


def _gitignore_covers(entry: str, text: str) -> bool:
    entry = entry.strip().lstrip("/")
    entry_as_dir = entry.rstrip("/")
    for raw_line in text.splitlines():
        pattern = raw_line.strip()
        if not pattern or pattern.startswith("#") or pattern.startswith("!"):
            continue
        pattern = pattern.lstrip("/")
        pattern_as_dir = pattern.rstrip("/")
        if pattern == entry or pattern_as_dir == entry_as_dir:
            return True
        if entry.startswith(pattern_as_dir + "/"):
            return True
    return False
