from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def render_json(payload: Dict[str, Any], *, compact: bool) -> str:
    if compact:
        return json.dumps(payload, sort_keys=True)
    return json.dumps(payload, indent=2, sort_keys=True)


def render_index_summary(payload: Dict[str, Any]) -> str:
    files = payload.get("files_indexed", 0)
    symbols = payload.get("symbols_indexed", 0)
    edges = payload.get("edges_indexed", 0)
    parse_errors = payload.get("parse_errors") or {}
    db = _display_path(str(payload.get("db_path", "")), str(payload.get("repo_root", "")))

    progress = [f"Parsing: {files:,} files"]
    indexing = f"Indexing: {symbols:,} symbols, {edges:,} edges"
    if parse_errors:
        indexing += f" ({len(parse_errors)} parse errors)"
    progress.append(indexing)

    detail = [
        "",
        f"  Files:   {files:,}",
        f"  Symbols: {symbols:,}",
        f"  Edges:   {edges:,}",
        f"  Profile: {payload.get('profile', '')}",
        f"  DB:      {db}",
    ]
    detail.extend(_render_parse_errors(parse_errors))

    return "\n".join(progress + detail) + "\n"


def render_refresh_summary(payload: Dict[str, Any]) -> str:
    changed = len(payload.get("changed_files") or [])
    deleted = len(payload.get("deleted_files") or [])
    unchanged = len(payload.get("unchanged_files") or [])
    symbols = payload.get("symbols_indexed", 0)
    edges = payload.get("edges_indexed", 0)
    parse_errors = payload.get("parse_errors") or {}
    db = _display_path(str(payload.get("db_path", "")), str(payload.get("repo_root", "")))

    progress = [f"Scanning: {changed + deleted + unchanged:,} files"]
    if changed or deleted:
        progress.append(f"Indexing: {symbols:,} symbols, {edges:,} edges")

    detail = [
        "",
        f"  Changed:   {changed:,}",
        f"  Deleted:   {deleted:,}",
        f"  Unchanged: {unchanged:,}",
    ]
    if changed or deleted:
        detail.extend(
            [
                f"  Symbols:   {symbols:,}",
                f"  Edges:     {edges:,}",
            ]
        )
    detail.extend(
        [
            f"  Profile:   {payload.get('profile', '')}",
            f"  DB:        {db}",
        ]
    )
    detail.extend(_render_parse_errors(parse_errors))

    return "\n".join(progress + detail) + "\n"


def render_context_markdown(payload: Dict[str, Any]) -> str:
    lines: List[str] = [
        "# csegraph context",
        "",
        f"Query: {payload['query']}",
        f"Target: `{payload['target']}`",
        f"Total estimated tokens: {payload['total_estimated_tokens']}",
        f"Sufficient: {payload['sufficiency']['sufficient']}",
        "",
    ]
    for rank, node in enumerate(payload["nodes"], start=1):
        lines.extend(_render_node(rank, node))
    return "\n".join(lines).rstrip() + "\n"


def _render_node(rank: int, node: Dict[str, Any]) -> List[str]:
    path = node["path"]
    line_range = _line_range_text(node.get("line_range"))
    lines = [
        f"## {rank}. `{node['id']}`",
        "",
        f"- Kind: `{node['kind']}`",
        f"- Path: `{path}{line_range}`",
        f"- Reasons: {', '.join(node['reason'])}",
        f"- Estimated tokens: {node['estimated_tokens']}",
    ]
    if node.get("explanation"):
        lines.append(f"- Explanation: {node['explanation']}")
    if node.get("summary"):
        lines.extend(["", node["summary"]])
    if node.get("source_text") is not None:
        lines.extend(["", f"```{node['language']}", node["source_text"].rstrip(), "```"])
    lines.append("")
    return lines


def _line_range_text(line_range: Optional[List[int]]) -> str:
    if not line_range:
        return ""
    return f":{line_range[0]}-{line_range[1]}"


def _display_path(path: str, repo_root: str) -> str:
    resolved = Path(path).resolve()
    root = Path(repo_root).resolve()
    try:
        return str(resolved.relative_to(root))
    except ValueError:
        return str(resolved)


def _render_parse_errors(parse_errors: Dict[str, str]) -> List[str]:
    if not parse_errors:
        return []
    lines = ["  Errors:"]
    for path, error in sorted(parse_errors.items()):
        lines.append(f"    {path}: {error}")
    return lines
