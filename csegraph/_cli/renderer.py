"""Render the compact CseGraph 2.0 CLI contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def render_json(payload: dict[str, Any], *, compact: bool) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":") if compact else None,
        indent=None if compact else 2,
    )


def render_index_summary(payload: dict[str, Any]) -> str:
    lines = [
        (
            f"Index: {payload.get('files_indexed', 0):,} files, "
            f"{payload.get('symbols_indexed', 0):,} symbols, "
            f"{payload.get('edges_indexed', 0):,} edges"
        )
    ]
    _append_errors_and_warnings(lines, payload)
    return "\n".join(lines) + "\n"


def render_refresh_summary(payload: dict[str, Any]) -> str:
    lines = [
        (
            f"Refresh: {len(payload.get('changed_files') or []):,} changed, "
            f"{len(payload.get('deleted_files') or []):,} deleted, "
            f"{len(payload.get('unchanged_files') or []):,} unchanged"
        )
    ]
    _append_errors_and_warnings(lines, payload)
    return "\n".join(lines) + "\n"


def render_context_markdown(payload: dict[str, Any]) -> str:
    if payload.get("schema_version") != "csegraph-context-v5":
        raise ValueError("Only csegraph-context-v5 can be rendered")

    lines = [
        "# csegraph context",
        "",
        f"Status: `{payload.get('status', '')}`",
        "",
    ]
    warnings = payload.get("warnings") or []
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")

    candidates = payload.get("candidates") or []
    if candidates:
        lines.extend(["## Candidates", ""])
        for candidate in candidates:
            location = _location(candidate.get("path"), candidate.get("lines"))
            lines.append(f"- Candidate `{candidate.get('id', '')}` at `{location}`")
        lines.append("")

    slices = payload.get("slices") or []
    if slices:
        lines.extend(["## Slices", ""])
        for slice_ in slices:
            location = _location(slice_.get("path"), slice_.get("lines"))
            role = slice_.get("role") or "context"
            symbol = slice_.get("symbol") or ""
            lines.extend(
                [
                    f"### `{location}` — `{symbol}` ({role})",
                    "",
                    "```",
                    str(slice_.get("code") or "").rstrip(),
                    "```",
                    "",
                ]
            )

    missing = payload.get("missing") or []
    if missing:
        lines.extend(["## Missing information", ""])
        lines.extend(f"- `{json.dumps(item, sort_keys=True)}`" for item in missing)
        lines.append("")

    next_action = payload.get("next")
    if next_action:
        lines.extend(["## Next", "", f"- Tool: `{next_action.get('tool', '')}`"])
        if next_action.get("arguments"):
            lines.append(f"- Arguments: `{json.dumps(next_action['arguments'], sort_keys=True)}`")
        if next_action.get("reason"):
            lines.append(f"- Reason: {next_action['reason']}")
        lines.append("")

    if payload.get("diagnostics") is not None:
        lines.extend(
            [
                "## Diagnostics",
                "",
                "```json",
                json.dumps(payload["diagnostics"], indent=2, sort_keys=True),
                "```",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_status_summary(payload: dict[str, Any]) -> str:
    languages = ", ".join(payload.get("languages") or []) or "none"
    lines = [
        (
            f"Status: {payload.get('total_files', 0):,} files, "
            f"{payload.get('total_nodes', 0):,} entities, "
            f"{payload.get('total_edges', 0):,} edges"
        ),
        f"Schema: {payload.get('schema_version', '')}",
        f"Languages: {languages}",
    ]
    for warning in payload.get("warnings") or []:
        lines.append(f"WARNING: {warning}")
    return "\n".join(lines) + "\n"


def render_install_summary(payload: dict[str, Any]) -> str:
    state = payload.get("state")
    if state:
        return f"MCP doctor: {state}\n"
    installed = payload.get("installed") or []
    skipped = payload.get("skipped") or []
    lines = [f"MCP install: {len(installed)} configured, {len(skipped)} skipped"]
    for step in payload.get("next_steps") or []:
        lines.append(f"- {step}")
    return "\n".join(lines) + "\n"


def _append_errors_and_warnings(lines: list[str], payload: dict[str, Any]) -> None:
    for path, error in sorted((payload.get("parse_errors") or {}).items()):
        lines.append(f"ERROR {path}: {error}")
    for warning in payload.get("warnings") or []:
        lines.append(f"WARNING: {warning}")


def _location(path: Any, line_range: Any) -> str:
    value = str(path or "")
    if isinstance(line_range, list) and len(line_range) == 2:
        return f"{value}:{line_range[0]}-{line_range[1]}"
    return value


def _display_path(path: str, repo_root: str) -> str:
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except (ValueError, OSError):
        return str(candidate)
