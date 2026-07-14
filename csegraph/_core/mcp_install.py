from __future__ import annotations

import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

from csegraph._core.core.models import McpInstallResult, McpInstallTarget
from csegraph._core.core.serializer import to_dict
from csegraph._core.mcp_resolve import build_mcp_server_entry
from csegraph._core.mcp_verify import verify_mcp_entry

Platform = Literal[
    "auto",
    "codex",
    "cursor",
    "claude-code",
    "gemini-cli",
    "kiro",
    "antigravity-cli",
    "antigravity-ide",
    "copilot",
    "vscode",
]


@dataclass(frozen=True)
class PlatformAdapter:
    path: Path
    section: str
    vscode_style: bool = False
    scope: Literal["project", "global"] = "project"
    include_cwd: bool = False


_PROJECT_JSON_TARGETS: dict[str, PlatformAdapter] = {
    "claude-code": PlatformAdapter(Path(".mcp.json"), "mcpServers"),
    "cursor": PlatformAdapter(Path(".cursor") / "mcp.json", "mcpServers"),
    "gemini-cli": PlatformAdapter(Path(".gemini") / "settings.json", "mcpServers"),
    "kiro": PlatformAdapter(Path(".kiro") / "settings" / "mcp.json", "mcpServers"),
    "antigravity-cli": PlatformAdapter(
        Path(".agents") / "mcp_config.json",
        "mcpServers",
        include_cwd=True,
    ),
    "antigravity-ide": PlatformAdapter(
        Path(".gemini") / "config" / "mcp_config.json",
        "mcpServers",
        scope="global",
        include_cwd=True,
    ),
    "copilot": PlatformAdapter(Path(".vscode") / "mcp.json", "servers", vscode_style=True),
}

_PLATFORMS = {"auto", "codex", "vscode", *_PROJECT_JSON_TARGETS}

_INSTRUCTION_BODY = """\
# CseGraph — Agent Instructions

CseGraph is a context engine: deliver the smallest accurate code slice for the
task, not whole-repo reads or broad grep. This repository has a csegraph index.
Use the visible csegraph MCP tools for code context instead of broad file reads
or grep scans.

## Workflow

1. Call `csegraph_context` directly with the task and target when known.
2. Use the returned compact `slices` before opening files or running text search.
3. Use `csegraph_minimal` only for explicit index-health or orientation requests.
4. Escalate to `csegraph_graph` or `csegraph_path` only when `next` recommends
   a focused structural operation.
5. Resolve ordinary tasks in one CseGraph call and structural tasks in at most two.
6. If context returns `refresh_required`, call `csegraph_refresh` before retrying.

## MCP enablement

- Use CseGraph only when this platform shows an enabled `csegraph` MCP server
  and the six CseGraph tools in its MCP/tools UI.
- If the tools are missing, ask the user to run
  `csegraph install --platform <this-platform>` and enable or approve the
  server in this platform before querying context.
- A local `.csegraph` index or another platform's MCP config is not enough;
  each host must install and enable its own CseGraph server entry.

## Tool boundary

- Use only advertised MCP tools: `csegraph_index`, `csegraph_refresh`,
  `csegraph_minimal`, `csegraph_context`, `csegraph_graph`, and
  `csegraph_path`.
- Do not invent host-specific tool names or call internal Python modules,
  SDKs, `python -c` MCP bridges, or private maintainer scripts.
- If MCP is unavailable, do not query `.csegraph/index.db` directly and do not
  use CLI context commands as a substitute for the host MCP server. Use only
  setup/health commands such as `csegraph install`, `csegraph doctor`,
  `csegraph status`, `csegraph index`, and `csegraph refresh` until this
  platform's MCP server is enabled.
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
    "antigravity-cli": ("AGENTS.md", "GEMINI.md"),
    "antigravity-ide": ("AGENTS.md", "GEMINI.md"),
    "copilot": ("AGENTS.md",),
    "vscode": ("AGENTS.md",),
}


_RUNTIME_GITIGNORE_ENTRIES = (".csegraph/", ".csegraphinclude")


def _csegraph_hook_command(command: str, args: Sequence[str]) -> str:
    argv = [command, *args]
    if sys.platform.startswith("win"):
        command_line = subprocess.list2cmdline(argv)
        return subprocess.list2cmdline(
            ["cmd", "/D", "/C", f"{command_line} >NUL 2>NUL || exit /B 0"]
        )
    return f"{shlex.join(argv)} >/dev/null 2>&1 || true"


def _claude_hooks(command: str, repo: Path) -> dict[str, Any]:
    return {
        "hooks": {
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": _csegraph_hook_command(
                                command,
                                [
                                    "refresh",
                                    str(repo),
                                ],
                            ),
                        }
                    ],
                }
            ]
        }
    }


def _codex_hooks(command: str, repo: Path) -> dict[str, Any]:
    return {
        "hooks": {
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": _csegraph_hook_command(
                                command,
                                [
                                    "refresh",
                                    str(repo),
                                ],
                            ),
                            "timeout": 120,
                            "statusMessage": "Refreshing CseGraph index after the agent turn",
                        }
                    ],
                }
            ]
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


def _replace_toml_table(existing: str, name: str, replacement: str) -> str:
    """Replace one top-level TOML table without requiring a TOML writer."""

    lines = existing.splitlines(keepends=True)
    header = f"[{name}]"
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if start is None:
            if stripped == header:
                start = index
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            end = index
            break

    if start is not None:
        del lines[start:end]

    prefix = "".join(lines).rstrip()
    if prefix:
        prefix += "\n\n"
    return prefix + replacement.rstrip() + "\n"


def _vscode_tasks(command: str) -> list[dict[str, Any]]:
    return [
        {
            "label": "csegraph: Build Index",
            "type": "process",
            "command": command,
            "args": ["index"],
            "group": "build",
            "problemMatcher": [],
        },
        {
            "label": "csegraph: Refresh",
            "type": "process",
            "command": command,
            "args": ["refresh"],
            "problemMatcher": [],
        },
        {
            "label": "csegraph: Status",
            "type": "process",
            "command": command,
            "args": ["status", "--verbose"],
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
        verify: bool = False,
    ) -> McpInstallResult:
        if platform not in _PLATFORMS:
            raise ValueError(f"Unsupported MCP install platform: {platform}")
        server_entry = self._server_entry(
            vscode_style=False,
            platform=None if platform == "auto" else platform,
        )

        result = McpInstallResult(
            command="install",
            repo_root=str(self.repo),
            server_name="csegraph",
            server_command=str(server_entry["command"]),
            server_args=list(server_entry["args"]),
            dry_run=dry_run,
            next_steps=_install_next_steps(platform, self.repo),
        )

        if platform == "auto":
            self._install_codex(dry_run, result)
            for candidate in (
                "claude-code",
                "cursor",
                "gemini-cli",
                "kiro",
                "antigravity-cli",
                "copilot",
            ):
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
        if verify and not dry_run:
            result.verification = to_dict(verify_mcp_entry(server_entry))
        elif verify and dry_run:
            result.verification = {"state": "skipped", "reason": "dry_run"}

        return result

    def _install_project_json(
        self,
        platform: str,
        dry_run: bool,
        result: McpInstallResult,
        *,
        force: bool,
    ) -> None:
        adapter = _PROJECT_JSON_TARGETS[platform]
        path = self._adapter_root(adapter) / adapter.path
        if not force and not path.exists() and not path.parent.exists():
            result.skipped.append(
                McpInstallTarget(
                    platform=platform,
                    path=str(path),
                    scope=adapter.scope,
                    action="skipped",
                    dry_run=dry_run,
                    reason="platform config not present",
                )
            )
            return

        action = "updated" if path.exists() else "created"
        if not dry_run:
            data = _read_json_object(path)
            servers = data.setdefault(adapter.section, {})
            servers["csegraph"] = self._server_entry(
                vscode_style=adapter.vscode_style,
                include_cwd=adapter.include_cwd,
                platform=platform,
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        result.installed.append(
            McpInstallTarget(
                platform=platform,
                path=str(path),
                scope=adapter.scope,
                action=action,
                dry_run=dry_run,
            )
        )

    def _install_codex(self, dry_run: bool, result: McpInstallResult) -> None:
        path = self.repo / ".codex" / "config.toml"
        action = "updated" if path.exists() else "created"

        if not dry_run:
            entry = self._server_entry(vscode_style=False, platform="codex")
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            args = ", ".join(json.dumps(value) for value in entry["args"])
            table = (
                "[mcp_servers.csegraph]\n"
                f"command = {json.dumps(entry['command'])}\n"
                f"args = [{args}]\n"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                _replace_toml_table(existing, "mcp_servers.csegraph", table),
                encoding="utf-8",
            )

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
            data["csegraph.command"] = self._server_entry(vscode_style=False)["command"]
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
            tasks_data.setdefault("version", "2.0.1")
            tasks_list = tasks_data.setdefault("tasks", [])
            existing_labels = {t.get("label") for t in tasks_list if isinstance(t, dict)}
            for task in _vscode_tasks(self._server_entry(vscode_style=False)["command"]):
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
                hook_data = cfg["build"](
                    self._server_entry(vscode_style=False)["command"], self.repo
                )
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

    def _server_entry(
        self,
        *,
        vscode_style: bool,
        include_cwd: bool = False,
        platform: str | None = None,
    ) -> dict[str, Any]:
        return build_mcp_server_entry(
            self.repo,
            command=self.command,
            vscode_style=vscode_style,
            include_cwd=include_cwd,
            platform=platform,
        )

    def _adapter_root(self, adapter: PlatformAdapter) -> Path:
        return self.home if adapter.scope == "global" else self.repo


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
        entries.extend(
            _HOOK_CONFIGS[name]["path"].as_posix() for name in _hook_platforms(hook_platform)
        )

    return tuple(dict.fromkeys(entries))


def _platform_config_gitignore_entries(platform: str) -> tuple[str, ...]:
    if platform == "auto":
        entries = [".codex/config.toml"]
        entries.extend(
            target.path.as_posix()
            for target in _PROJECT_JSON_TARGETS.values()
            if target.scope == "project"
        )
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
        target = _PROJECT_JSON_TARGETS[platform]
        if target.scope == "global":
            return ()
        return (target.path.as_posix(),)
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


def _install_next_steps(platform: str, repo: Path) -> list[str]:
    install_steps = [
        (
            f"Run `csegraph install --platform {platform}` again if you need to "
            "refresh the client config."
        ),
        (
            "Open each configured client's MCP/tools settings and enable or "
            "approve the csegraph server."
        ),
        (
            "Confirm the six CseGraph tools are visible: csegraph_index, "
            "csegraph_refresh, csegraph_minimal, csegraph_context, "
            "csegraph_graph, and csegraph_path."
        ),
    ]
    if platform == "auto":
        return install_steps + [
            (
                f"Run `csegraph doctor {repo} --platform auto --json` after "
                "each host has called a CseGraph tool."
            ),
        ]
    if platform == "vscode":
        return install_steps + [
            "Reload VS Code after installing or enabling the CseGraph extension recommendation.",
            "Confirm the CseGraph status bar and commands are available in the workspace.",
            f"Run `csegraph status {repo}` if the extension reports a stale or missing index.",
        ]
    return install_steps + [
        (
            f"Run `csegraph doctor {repo} --platform {platform} --json` after "
            "the host has called a CseGraph tool."
        ),
    ]
