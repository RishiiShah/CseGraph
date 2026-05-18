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
]

_PROJECT_JSON_TARGETS = {
    "claude-code": (Path(".mcp.json"), "mcpServers", False),
    "cursor": (Path(".cursor") / "mcp.json", "mcpServers", False),
    "gemini-cli": (Path(".gemini") / "settings.json", "mcpServers", False),
    "kiro": (Path(".kiro") / "settings" / "mcp.json", "mcpServers", False),
    "copilot": (Path(".vscode") / "mcp.json", "servers", True),
}

_PLATFORMS = {"auto", "codex", *_PROJECT_JSON_TARGETS}


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

    def install(self, *, platform: Platform = "auto", dry_run: bool = False) -> McpInstallResult:
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
            return result

        if platform == "codex":
            self._install_codex(dry_run, result)
            return result

        self._install_project_json(platform, dry_run, result, force=True)
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

