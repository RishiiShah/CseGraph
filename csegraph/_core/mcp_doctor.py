from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, cast

from csegraph._core.core.models import McpDoctorAggregateResult, McpDoctorResult, to_dict
from csegraph._core.mcp_install import _PROJECT_JSON_TARGETS, Platform
from csegraph._core.mcp_resolve import McpLauncherResolutionError, build_mcp_server_entry
from csegraph._core.mcp_verify import verify_mcp_entry

_PROJECT_SCOPED_DOCTOR_PLATFORMS: tuple[str, ...] = (
    "codex",
    "claude-code",
    "cursor",
    "gemini-cli",
    "kiro",
    "antigravity-cli",
    "copilot",
    "vscode",
)

_HOST_VERIFICATION: dict[str, list[str]] = {
    "codex": [
        "Open `/mcp` or Codex MCP/tools settings, enable the csegraph server and tools, then ask Codex to call csegraph_minimal."
    ],
    "claude-code": [
        "Run `claude mcp get csegraph` or open `/mcp`, approve and enable the project server/tools if prompted, then ask Claude Code to call csegraph_minimal."
    ],
    "cursor": [
        "Open Cursor Settings → MCP, enable the csegraph server, and confirm its six tools are visible."
    ],
    "kiro": [
        "Enable MCP support in Kiro settings, enable the csegraph server, then confirm its tools are visible in the MCP panel."
    ],
    "gemini-cli": ["Run the Gemini MCP listing command if installed, then use `/mcp` to enable csegraph and confirm its tools are visible."],
    "antigravity-cli": ["Run Antigravity CLI in this workspace, enable csegraph in its MCP server list, and confirm the tools are visible."],
    "antigravity-ide": ["Open Antigravity's MCP configuration UI, enable the global csegraph server, and confirm the tools are visible."],
    "copilot": ["In VS Code, run `MCP: List Servers`, start or enable csegraph, then confirm Agent Mode shows the six CseGraph tools."],
    "vscode": ["Open the CseGraph VS Code extension status and confirm it uses the resolved CLI command."],
}


class McpDoctorService:
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

    def doctor(
        self,
        *,
        platform: Platform,
        require_observed_call: bool = False,
        verify: bool = True,
    ) -> McpDoctorResult:
        config_path, config_present, config_entry = self._read_config(platform)
        try:
            server_entry = config_entry or build_mcp_server_entry(
                self.repo,
                command=self.command,
                platform=platform if platform != "vscode" else None,
            )
            launcher = str(server_entry.get("command") or "")
            launcher_present = _launcher_present(launcher)
        except McpLauncherResolutionError as exc:
            server_entry = {
                "type": "stdio",
                "command": self.command,
                "args": ["serve", "--repo", str(self.repo), "--platform", platform],
            }
            launcher = self.command
            launcher_present = False
            resolution_error = str(exc)
        else:
            resolution_error = None

        contract_issues = (
            _contract_issues(server_entry, platform=platform, repo=self.repo)
            if config_present
            else []
        )
        contract_valid = config_present and not contract_issues
        verification: dict[str, Any] = {}
        protocol_verified = False
        if not config_present:
            verification = {"state": "config_missing"}
        elif resolution_error is not None:
            verification = {
                "state": "launcher_missing",
                "error": resolution_error,
            }
        elif verify and launcher_present:
            verification = to_dict(verify_mcp_entry(server_entry))
            protocol_verified = (
                verification.get("state") == "protocol_verified" and contract_valid
            )
        elif verify:
            verification = {
                "state": "launcher_missing",
                "error": f"MCP launcher does not exist: {launcher}",
            }

        observed_call = _has_observed_host_call(self.repo, platform=platform)
        state = _doctor_state(
            config_present=config_present,
            launcher_present=launcher_present,
            protocol_verified=protocol_verified,
            observed_call=observed_call,
            require_observed_call=require_observed_call,
        )
        recommendations = _recommendations(
            platform=platform,
            config_present=config_present,
            launcher_present=launcher_present,
            contract_valid=contract_valid,
            protocol_verified=protocol_verified,
            observed_call=observed_call,
            require_observed_call=require_observed_call,
        )
        return McpDoctorResult(
            command="doctor",
            repo_root=str(self.repo),
            platform=platform,
            state=state,
            config_path=str(config_path) if config_path is not None else None,
            config_present=config_present,
            launcher_present=launcher_present,
            contract_valid=contract_valid,
            contract_issues=contract_issues,
            protocol_verified=protocol_verified,
            observed_call=observed_call,
            require_observed_call=require_observed_call,
            server_entry=server_entry,
            verification=verification,
            host_verification=_HOST_VERIFICATION.get(platform, []),
            recommendations=recommendations,
        )

    def doctor_all(
        self,
        *,
        require_observed_call: bool = False,
        verify: bool = True,
    ) -> McpDoctorAggregateResult:
        results = [
            self.doctor(
                platform=cast(Platform, platform),
                require_observed_call=require_observed_call,
                verify=verify,
            )
            for platform in _PROJECT_SCOPED_DOCTOR_PLATFORMS
        ]
        configured = [result for result in results if result.config_present]
        missing = [result for result in results if not result.config_present]
        launcher_missing = [result for result in configured if result.state == "launcher_missing"]
        contract_invalid = [result for result in configured if not result.contract_valid]
        protocol_verified = [
            result for result in configured if result.protocol_verified or result.observed_call
        ]
        observed = [result for result in configured if result.observed_call]
        state = _aggregate_state(
            configured=configured,
            launcher_missing=launcher_missing,
            require_observed_call=require_observed_call,
        )
        return McpDoctorAggregateResult(
            command="doctor",
            repo_root=str(self.repo),
            platform="auto",
            state=state,
            configured_count=len(configured),
            missing_count=len(missing),
            launcher_missing_count=len(launcher_missing),
            contract_invalid_count=len(contract_invalid),
            protocol_verified_count=len(protocol_verified),
            observed_call_count=len(observed),
            require_observed_call=require_observed_call,
            platforms=results,
            recommendations=_aggregate_recommendations(
                configured=configured,
                missing=missing,
                launcher_missing=launcher_missing,
                contract_invalid=contract_invalid,
                require_observed_call=require_observed_call,
            ),
        )

    def _read_config(self, platform: str) -> tuple[Path | None, bool, dict[str, Any] | None]:
        if platform == "codex":
            path = self.repo / ".codex" / "config.toml"
            if not path.exists():
                return path, False, None
            try:
                import tomllib
            except ModuleNotFoundError:  # pragma: no cover - Python <3.11 fallback
                import tomli as tomllib  # type: ignore[no-redef]

            data = tomllib.loads(path.read_text(encoding="utf-8"))
            entry = ((data.get("mcp_servers") or {}).get("csegraph") or {})
            return path, bool(entry), _normal_entry(entry) if entry else None

        if platform == "vscode":
            path = self.repo / ".vscode" / "settings.json"
            if not path.exists():
                return path, False, None
            data = _read_json_object(path)
            command = data.get("csegraph.command")
            if not command:
                return path, False, None
            return path, True, {
                "type": "stdio",
                "command": command,
                "args": ["serve", "--repo", str(self.repo), "--platform", platform],
            }

        adapter = _PROJECT_JSON_TARGETS.get(platform)
        if adapter is None:
            return None, False, None
        root = self.home if adapter.scope == "global" else self.repo
        path = root / adapter.path
        if not path.exists():
            return path, False, None
        data = _read_json_object(path)
        entry = ((data.get(adapter.section) or {}).get("csegraph") or {})
        return path, bool(entry), _normal_entry(entry) if entry else None


def _normal_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    normalized = {
        "type": entry.get("type", "stdio"),
        "command": entry.get("command"),
        "args": list(entry.get("args") or []),
    }
    if entry.get("cwd"):
        normalized["cwd"] = entry["cwd"]
    return normalized


def _contract_issues(entry: dict[str, Any], *, platform: str, repo: Path) -> list[str]:
    issues: list[str] = []
    command = str(entry.get("command") or "")
    args = [str(arg) for arg in (entry.get("args") or [])]
    if entry.get("type", "stdio") != "stdio":
        issues.append("transport type must be stdio")
    if not command:
        issues.append("command is missing")
    elif not Path(command).is_absolute():
        issues.append("command must be an absolute csegraph executable path")
    expected_args = ["serve", "--repo", str(repo), "--platform", platform]
    if platform == "vscode":
        expected_args = ["serve", "--repo", str(repo)]
    if args != expected_args:
        issues.append(f"args must be {expected_args!r}")
    if _platform_requires_cwd(platform) and entry.get("cwd") != str(repo):
        issues.append(f"cwd must be '{repo}' for this platform")
    joined = " ".join([command, *args])
    banned_patterns = (
        "python -c",
        "ClientSession",
        "StdioServerParameters",
        "csegraph._core",
        "-m csegraph._cli",
        "env/bin/python",
    )
    for pattern in banned_patterns:
        if pattern in joined:
            issues.append(f"generated config must not use {pattern}")
    return issues


def _platform_requires_cwd(platform: str) -> bool:
    adapter = _PROJECT_JSON_TARGETS.get(platform)
    return bool(adapter and adapter.include_cwd)


def _launcher_present(command: str) -> bool:
    if not command:
        return False
    if "/" in command or "\\" in command:
        path = Path(command).expanduser()
        return path.is_absolute() and path.is_file()
    return shutil.which(command) is not None


def _read_json_object(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"MCP config must be a JSON object: {path}")
    return data


def _has_observed_host_call(repo: Path, *, platform: str) -> bool:
    path = repo / ".csegraph" / "mcp_sessions.jsonl"
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            event.get("tool")
            and event.get("success") is True
            and event.get("platform") == platform
        ):
            return True
    return False


def _doctor_state(
    *,
    config_present: bool,
    launcher_present: bool,
    protocol_verified: bool,
    observed_call: bool,
    require_observed_call: bool,
) -> str:
    if not config_present:
        return "config_missing"
    if not launcher_present:
        return "launcher_missing"
    if observed_call:
        return "host_call_observed"
    if require_observed_call and protocol_verified:
        return "pending_host_approval"
    if protocol_verified:
        return "protocol_verified"
    return "config_written"


def _aggregate_state(
    *,
    configured: list[McpDoctorResult],
    launcher_missing: list[McpDoctorResult],
    require_observed_call: bool,
) -> str:
    if not configured:
        return "config_missing"
    if launcher_missing:
        return "launcher_missing"
    if all(result.observed_call for result in configured):
        return "host_call_observed"
    if require_observed_call and any(
        result.protocol_verified and not result.observed_call for result in configured
    ):
        return "pending_host_approval"
    if all(result.protocol_verified or result.observed_call for result in configured):
        return "protocol_verified"
    return "config_written"


def _aggregate_recommendations(
    *,
    configured: list[McpDoctorResult],
    missing: list[McpDoctorResult],
    launcher_missing: list[McpDoctorResult],
    contract_invalid: list[McpDoctorResult],
    require_observed_call: bool,
) -> list[str]:
    recommendations: list[str] = []
    if not configured:
        recommendations.append("Run `csegraph install --platform auto` to write project-scoped MCP configs.")
    elif missing:
        recommendations.append(
            "Some project-scoped clients are not configured; run `csegraph install --platform auto` if you want all of them."
        )
    if launcher_missing:
        platforms = ", ".join(result.platform for result in launcher_missing)
        recommendations.append(
            f"Re-run install for {platforms} with --command /absolute/path/to/csegraph."
        )
    elif contract_invalid:
        platforms = ", ".join(result.platform for result in contract_invalid)
        recommendations.append(
            f"Re-run install for {platforms} so their configs use the native absolute CLI plus `serve --repo` contract."
        )
    if require_observed_call and any(
        result.protocol_verified and not result.observed_call for result in configured
    ):
        recommendations.append(
            "Restart each configured host, enable/approve csegraph in its MCP/tools UI, then ask it to call csegraph_minimal."
        )
    return recommendations


def _recommendations(
    *,
    platform: str,
    config_present: bool,
    launcher_present: bool,
    contract_valid: bool,
    protocol_verified: bool,
    observed_call: bool,
    require_observed_call: bool,
) -> list[str]:
    recommendations: list[str] = []
    if not config_present:
        recommendations.append(f"Run `csegraph install --platform {platform}`.")
        return recommendations
    if not launcher_present:
        recommendations.append("Re-run install with --command /absolute/path/to/csegraph.")
    if not contract_valid:
        recommendations.append(
            f"Re-run `csegraph install --platform {platform}` so the config uses an absolute csegraph command and `serve --repo`."
        )
    if contract_valid and not protocol_verified:
        recommendations.append("Run `csegraph doctor --platform {}` after fixing the launcher.".format(platform))
    if require_observed_call and not observed_call:
        recommendations.extend(_HOST_VERIFICATION.get(platform, []))
    return recommendations
