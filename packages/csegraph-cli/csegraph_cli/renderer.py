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
    cache_hits = payload.get("cache_hits", 0)
    cache_misses = payload.get("cache_misses", 0)

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
        f"  Cache:   {cache_hits:,} hits, {cache_misses:,} misses",
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
    cache_hits = payload.get("cache_hits", 0)
    cache_misses = payload.get("cache_misses", 0)

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
            f"  Cache:     {cache_hits:,} hits, {cache_misses:,} misses",
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
        phases = (step.get("stats") or {}).get("phases") or {}
        for phase, elapsed_ms in phases.items():
            lines.append(f"  {phase:<18} {elapsed_ms:>9.3f}")
    lines.extend(
        [
            "",
            f"Total: {payload.get('total_elapsed_ms', 0):.3f} ms",
            f"DB: {_display_path(str(payload.get('db_path', '')), str(payload.get('repo_root', '')))}",
            f"Graph: {_display_path(str(payload.get('graph_output_path', '')), str(payload.get('repo_root', '')))}",
        ]
    )
    return "\n".join(lines) + "\n"


def render_communities_summary(payload: Dict[str, Any]) -> str:
    lines = [
        f"Communities: {payload.get('num_communities', 0)} detected  "
        f"(modularity {payload.get('modularity', 0):.4f})",
        "",
    ]
    for comm in payload.get("communities", []):
        lines.append(f"  [{comm['id']}] {comm['size']:,} nodes — {comm.get('label', '')}")
    lines.append("")
    return "\n".join(lines)


def render_hooks_summary(payload: Dict[str, Any]) -> str:
    installed = payload.get("installed") or []
    skipped = payload.get("skipped") or []
    cmd = payload.get("command", "hooks")
    lines = [f"Hooks {cmd.split()[-1] if ' ' in cmd else cmd}:"]
    if installed:
        verb = "Installed" if "install" in cmd else "Removed"
        lines.append(f"  {verb}: {', '.join(installed)}")
    if skipped:
        lines.append(f"  Skipped:   {', '.join(skipped)}")
    lines.append(f"  Hooks dir: {payload.get('hooks_dir', '')}")
    lines.append("")
    return "\n".join(lines)


def render_status_summary(payload: Dict[str, Any]) -> str:
    lines = [
        f"Nodes: {payload.get('total_nodes', 0):,}",
        f"Edges: {payload.get('total_edges', 0):,}",
        f"Files: {payload.get('total_files', 0):,}",
        f"Languages: {', '.join(payload.get('languages', []))}",
        f"Schema: {payload.get('schema_version', '')}",
        f"Last updated: {payload.get('updated_at', 'unknown')}",
    ]
    if payload.get("built_branch"):
        lines.append(f"Built on branch: {payload['built_branch']}")
    if payload.get("built_commit"):
        lines.append(f"Built at commit: {payload['built_commit']}")
    parse_errors = payload.get("parse_errors") or {}
    if parse_errors:
        lines.append("Parse errors:")
        for path, error in sorted(parse_errors.items()):
            lines.append(f"  {path}: {error}")
    for warning in payload.get("warnings", []):
        lines.append(f"WARNING: {warning}")
    lines.append("")
    return "\n".join(lines)


def render_postprocess_summary(payload: Dict[str, Any]) -> str:
    parts = []
    if "fts" not in payload.get("skipped", []):
        parts.append(f"{payload.get('fts_entries', 0):,} FTS entries")
    if "communities" not in payload.get("skipped", []):
        parts.append(f"{payload.get('communities_detected', 0)} communities")
    return f"Post-processing: {', '.join(parts)}\n"


def render_path_summary(payload: Dict[str, Any]) -> str:
    if not payload.get("found"):
        return f"No path found between {payload.get('source', '?')} and {payload.get('target', '?')}.\n"
    nodes = payload.get("nodes") or []
    edges = payload.get("edges") or []
    lines = [f"Path ({payload.get('length', len(edges))} hops):", ""]
    for i, node in enumerate(nodes):
        lines.append(f"  {node.get('name', node.get('node_id', ''))} ({node.get('kind', '')})")
        if i < len(edges):
            lines.append(f"    --[{edges[i].get('relation', '?')}]-->")
    lines.append("")
    return "\n".join(lines)


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
    _render_corpus_check(lines, payload)
    _render_summary_tables(lines, payload)
    _render_god_nodes(lines, payload)
    _render_knowledge_gaps(lines, payload)
    _render_sections(lines, payload)
    _render_surprising(lines, payload)
    _render_questions(lines, payload)
    return "\n".join(lines)


def _render_corpus_check(lines: List[str], payload: Dict[str, Any]) -> None:
    lines.extend([
        "## Corpus Check", "",
        "| Metric | Count |", "|---|---:|",
        f"| Files | {payload['total_files']:,} |",
        f"| Symbols | {payload['total_symbols']:,} |",
        f"| Edges | {payload['total_edges']:,} |",
        f"| Parse errors | {payload['parse_error_count']:,} |",
        "",
    ])


def _render_summary_tables(lines: List[str], payload: Dict[str, Any]) -> None:
    lines.extend(["## Summary", ""])
    if payload.get("node_counts"):
        lines.extend(["### Nodes by type", "", "| Type | Count |", "|---|---:|"])
        for ntype, count in sorted(payload["node_counts"].items()):
            lines.append(f"| {ntype} | {count:,} |")
        lines.append("")
    if payload.get("edge_counts"):
        lines.extend(["### Edges by relation", "", "| Relation | Count |", "|---|---:|"])
        for relation, count in sorted(payload["edge_counts"].items()):
            lines.append(f"| {relation} | {count:,} |")
        lines.append("")


def _render_god_nodes(lines: List[str], payload: Dict[str, Any]) -> None:
    if not payload.get("god_nodes"):
        return
    lines.extend([
        "## God Nodes", "",
        "| Rank | Name | Kind | Path | Degree |",
        "|---:|---|---|---|---:|",
    ])
    for rank, node in enumerate(payload["god_nodes"], start=1):
        lines.append(
            f"| {rank} | `{node['name']}` | {node['kind']} "
            f"| {node['path']} | {node['degree']} |"
        )
    lines.append("")


def _render_gap_table(lines: List[str], gaps: List[Dict[str, Any]], reason: Optional[str] = None) -> None:
    lines.extend(["| Name | Kind | Path | Degree |", "|---|---|---|---:|"])
    for node in gaps:
        if reason is not None and node.get("reason") != reason:
            continue
        lines.append(f"| `{node['name']}` | {node['kind']} | {node['path']} | {node['degree']} |")


def _render_knowledge_gaps(lines: List[str], payload: Dict[str, Any]) -> None:
    if not payload.get("knowledge_gaps"):
        return
    lines.extend(["## Knowledge Gaps", ""])
    groups = payload.get("knowledge_gap_groups") or []
    if groups:
        for group in groups:
            lines.extend([f"### {group['label']}", ""])
            if group.get("description"):
                lines.extend([group["description"], ""])
            _render_gap_table(lines, payload["knowledge_gaps"], reason=group["reason"])
            lines.append("")
    else:
        _render_gap_table(lines, payload["knowledge_gaps"])
    lines.append("")


def _render_sections(lines: List[str], payload: Dict[str, Any]) -> None:
    if not payload.get("sections"):
        return
    lines.extend([
        "## Sections", "",
        "| Section | Files | Symbols | Internal edges | Cross-section deps |",
        "|---|---:|---:|---:|---|",
    ])
    for section in payload["sections"]:
        deps = ", ".join(section.get("cross_section_deps", []))
        lines.append(
            f"| `{section['name']}` | {section['files']:,} "
            f"| {section['symbols']:,} | {section['internal_edges']:,} "
            f"| {deps} |"
        )
    lines.append("")


def _render_surprising(lines: List[str], payload: Dict[str, Any]) -> None:
    if not payload.get("surprising_connections"):
        return
    lines.extend(["## Surprising Connections", "", "| Source | Relation | Target |", "|---|---|---|"])
    for edge in payload["surprising_connections"]:
        lines.append(
            f"| `{edge['source_path']}` | {edge['relation']} "
            f"| `{edge['target_path']}` |"
        )
    lines.append("")


def _render_questions(lines: List[str], payload: Dict[str, Any]) -> None:
    if not payload.get("suggested_questions"):
        return
    lines.extend(["## Suggested Questions", ""])
    for question in payload["suggested_questions"]:
        lines.append(f"- {question}")
    lines.append("")


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
    if "raw_tokens" in stats:
        return (
            f"raw={stats['raw_tokens']}, context={stats['context_tokens']}, "
            f"reduction={stats['reduction_percent']}%"
        )
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
