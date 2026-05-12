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


def render_visual_export_summary(payload: Dict[str, Any]) -> str:
    return f"Graph file created at: {payload['output_path']}\n"


def render_benchmark_summary(payload: Dict[str, Any]) -> str:
    repo = _display_path(str(payload.get("repo_root", "")), str(payload.get("repo_root", "")))
    lines = [
        f"Benchmark: {repo}",
        "",
        "Step      Time (ms)  Stats",
        "--------  ---------  -----",
    ]
    for step in payload.get("steps", []):
        lines.append(
            f"{step['name']:<8}  {step['elapsed_ms']:>9.3f}  {_benchmark_stats(step.get('stats') or {})}"
        )
    lines.extend(
        [
            "",
            f"Total: {payload.get('total_elapsed_ms', 0):.3f} ms",
            f"DB: {_display_path(str(payload.get('db_path', '')), str(payload.get('repo_root', '')))}",
            f"Graph: {_display_path(str(payload.get('graph_output_path', '')), str(payload.get('repo_root', '')))}",
        ]
    )
    return "\n".join(lines) + "\n"


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


def render_report_markdown(payload: Dict[str, Any]) -> str:
    lines: List[str] = ["# csegraph report", ""]

    lines.append("## Corpus Check")
    lines.append("")
    lines.append(f"| Metric | Count |")
    lines.append(f"|---|---:|")
    lines.append(f"| Files | {payload['total_files']:,} |")
    lines.append(f"| Symbols | {payload['total_symbols']:,} |")
    lines.append(f"| Edges | {payload['total_edges']:,} |")
    lines.append(f"| Parse errors | {payload['parse_error_count']:,} |")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    if payload.get("node_counts"):
        lines.append("### Nodes by type")
        lines.append("")
        lines.append("| Type | Count |")
        lines.append("|---|---:|")
        for ntype, count in sorted(payload["node_counts"].items()):
            lines.append(f"| {ntype} | {count:,} |")
        lines.append("")
    if payload.get("edge_counts"):
        lines.append("### Edges by relation")
        lines.append("")
        lines.append("| Relation | Count |")
        lines.append("|---|---:|")
        for relation, count in sorted(payload["edge_counts"].items()):
            lines.append(f"| {relation} | {count:,} |")
        lines.append("")

    if payload.get("god_nodes"):
        lines.append("## God Nodes")
        lines.append("")
        lines.append("| Rank | Name | Kind | Path | Degree |")
        lines.append("|---:|---|---|---|---:|")
        for rank, node in enumerate(payload["god_nodes"], start=1):
            lines.append(
                f"| {rank} | `{node['name']}` | {node['kind']} "
                f"| {node['path']} | {node['degree']} |"
            )
        lines.append("")

    if payload.get("knowledge_gaps"):
        lines.append("## Knowledge Gaps")
        lines.append("")
        groups = payload.get("knowledge_gap_groups") or []
        if groups:
            for group in groups:
                lines.append(f"### {group['label']}")
                lines.append("")
                if group.get("description"):
                    lines.append(group["description"])
                    lines.append("")
                lines.append("| Name | Kind | Path | Degree |")
                lines.append("|---|---|---|---:|")
                for node in payload["knowledge_gaps"]:
                    if node.get("reason") != group["reason"]:
                        continue
                    lines.append(
                        f"| `{node['name']}` | {node['kind']} "
                        f"| {node['path']} | {node['degree']} |"
                    )
                lines.append("")
        else:
            lines.append("| Name | Kind | Path | Degree |")
            lines.append("|---|---|---|---:|")
            for node in payload["knowledge_gaps"]:
                lines.append(
                    f"| `{node['name']}` | {node['kind']} "
                    f"| {node['path']} | {node['degree']} |"
                )
        lines.append("")

    if payload.get("sections"):
        lines.append("## Sections")
        lines.append("")
        lines.append("| Section | Files | Symbols | Internal edges | Cross-section deps |")
        lines.append("|---|---:|---:|---:|---|")
        for section in payload["sections"]:
            deps = ", ".join(section.get("cross_section_deps", []))
            lines.append(
                f"| `{section['name']}` | {section['files']:,} "
                f"| {section['symbols']:,} | {section['internal_edges']:,} "
                f"| {deps} |"
            )
        lines.append("")

    if payload.get("surprising_connections"):
        lines.append("## Surprising Connections")
        lines.append("")
        lines.append("| Source | Relation | Target |")
        lines.append("|---|---|---|")
        for edge in payload["surprising_connections"]:
            lines.append(
                f"| `{edge['source_path']}` | {edge['relation']} "
                f"| `{edge['target_path']}` |"
            )
        lines.append("")

    if payload.get("suggested_questions"):
        lines.append("## Suggested Questions")
        lines.append("")
        for question in payload["suggested_questions"]:
            lines.append(f"- {question}")
        lines.append("")

    return "\n".join(lines)


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


def _benchmark_stats(stats: Dict[str, Any]) -> str:
    preferred = (
        "files",
        "symbols",
        "edges",
        "nodes",
        "total_estimated_tokens",
        "knowledge_gaps",
        "surprising_connections",
        "parse_errors",
        "output_size_bytes",
    )
    parts = [f"{key}={stats[key]}" for key in preferred if key in stats]
    return ", ".join(parts)
