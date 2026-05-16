"""Deterministic project report generated from the SQLite index."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from csegraph_core.core.models import ReportResult
from csegraph_core.index.repository import ProjectIndex


class ReportService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def report(self) -> ReportResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]

            node_counts = _node_counts(index)
            edge_counts = _edge_counts(index)
            parse_error_count = _parse_error_count(index)

            total_files = node_counts.get("file", 0)
            total_symbols = sum(
                node_counts.get(t, 0) for t in ("class", "function", "method", "test")
            )
            total_edges = sum(edge_counts.values())

            node_info = _node_info(index)
            degree, surprising, sections = _edge_analysis(index, node_info)
            god_nodes = _god_nodes(degree, node_info)
            knowledge_gaps = _knowledge_gaps(degree, node_info)
            knowledge_gap_groups = _knowledge_gap_groups(knowledge_gaps)
            questions = _suggested_questions(god_nodes, knowledge_gaps, surprising)

            return ReportResult(
                command="report",
                db_path=self.db_path,
                repo_root=repo_root,
                total_files=total_files,
                total_symbols=total_symbols,
                total_edges=total_edges,
                parse_error_count=parse_error_count,
                node_counts=dict(sorted(node_counts.items())),
                edge_counts=dict(sorted(edge_counts.items())),
                god_nodes=god_nodes,
                knowledge_gaps=knowledge_gaps,
                knowledge_gap_groups=knowledge_gap_groups,
                surprising_connections=surprising,
                suggested_questions=questions,
                sections=sections,
            )
        finally:
            index.close()


def _node_counts(index: ProjectIndex) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in index.conn.execute(
        "SELECT type, COUNT(*) AS c FROM nodes GROUP BY type",
    ):
        counts[row["type"]] = row["c"]
    return counts


def _edge_counts(index: ProjectIndex) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in index.conn.execute(
        "SELECT relation, COUNT(*) AS c FROM edges GROUP BY relation",
    ):
        counts[row["relation"]] = row["c"]
    return counts


def _parse_error_count(index: ProjectIndex) -> int:
    row = index.conn.execute(
        "SELECT COUNT(*) AS c FROM nodes WHERE parse_status = 'error'",
    ).fetchone()
    return int(row["c"])


def _build_node_sections(
    node_info: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, str], Counter, Counter]:
    symbol_types = {"class", "function", "method", "test"}
    node_section: Dict[str, str] = {}
    section_files: Counter = Counter()
    section_symbols: Counter = Counter()
    for node_id, info in node_info.items():
        path = info.get("path", "")
        if not path:
            continue
        section = path.split("/")[0] if "/" in path else "(root)"
        node_section[node_id] = section
        if info["type"] == "file":
            section_files[section] += 1
        elif info["type"] in symbol_types:
            section_symbols[section] += 1
    return node_section, section_files, section_symbols


def _detect_surprising(
    src: str, tgt: str, relation: str,
    node_info: Dict[str, Dict[str, Any]],
    seen: Set[Tuple[str, str, str]],
) -> Optional[Dict[str, Any]]:
    key = (src, relation, tgt)
    if key in seen:
        return None
    src_path = node_info.get(src, {}).get("path", "")
    tgt_path = node_info.get(tgt, {}).get("path", "")
    if not src_path or not tgt_path or src_path == tgt_path:
        return None
    seen.add(key)
    src_pkg = src_path.split("/")[0] if "/" in src_path else ""
    tgt_pkg = tgt_path.split("/")[0] if "/" in tgt_path else ""
    if src_pkg and tgt_pkg and src_pkg != tgt_pkg:
        return {"source": src, "target": tgt, "relation": relation,
                "source_path": src_path, "target_path": tgt_path}
    return None


def _edge_analysis(
    index: ProjectIndex,
    node_info: Dict[str, Dict[str, Any]],
    surprising_limit: int = 10,
) -> Tuple[
    Dict[str, int],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
]:
    degree: Dict[str, int] = Counter()
    node_section, section_files, section_symbols = _build_node_sections(node_info)

    surprising_raw: List[Dict[str, Any]] = []
    seen_surprising: Set[Tuple[str, str, str]] = set()
    section_internal: Dict[str, int] = Counter()
    section_cross: Dict[str, Set[str]] = defaultdict(set)

    for row in index.conn.execute("SELECT source, target, relation FROM edges"):
        src, tgt, relation = row["source"], row["target"], row["relation"]
        degree[src] += 1
        degree[tgt] += 1

        hit = _detect_surprising(src, tgt, relation, node_info, seen_surprising)
        if hit is not None:
            surprising_raw.append(hit)

        src_sec = node_section.get(src)
        tgt_sec = node_section.get(tgt)
        if src_sec is not None and tgt_sec is not None:
            if src_sec == tgt_sec:
                section_internal[src_sec] += 1
            else:
                section_cross[src_sec].add(tgt_sec)
                section_cross[tgt_sec].add(src_sec)

    surprising_raw.sort(key=lambda e: (e["source"], e["relation"], e["target"]))
    surprising = surprising_raw[:surprising_limit]

    all_sections = sorted(set(section_files) | set(section_symbols))
    sections: List[Dict[str, Any]] = [
        {
            "name": section,
            "files": section_files.get(section, 0),
            "symbols": section_symbols.get(section, 0),
            "internal_edges": section_internal.get(section, 0),
            "cross_section_deps": sorted(section_cross.get(section, set())),
        }
        for section in all_sections
    ]

    return dict(degree), surprising, sections


def _node_info(index: ProjectIndex) -> Dict[str, Dict[str, Any]]:
    info: Dict[str, Dict[str, Any]] = {}
    for row in index.conn.execute(
        "SELECT id, type, name, path FROM nodes",
    ):
        info[row["id"]] = {
            "type": row["type"],
            "name": row["name"],
            "path": row["path"],
        }
    return info


def _god_nodes(
    degree: Dict[str, int],
    node_info: Dict[str, Dict[str, Any]],
    limit: int = 10,
) -> List[Dict[str, Any]]:
    ranked = sorted(degree.items(), key=lambda kv: (-kv[1], kv[0]))
    result: List[Dict[str, Any]] = []
    for node_id, deg in ranked:
        info = node_info.get(node_id, {})
        if _is_god_node_noise(info):
            continue
        result.append({
            "node_id": node_id,
            "name": info.get("name", ""),
            "kind": info.get("type", ""),
            "path": info.get("path", ""),
            "degree": deg,
        })
        if len(result) >= limit:
            break
    return result


_GOD_NODE_NOISY_FILES = frozenset({"__init__.py", "__main__.py"})


def _is_god_node_noise(info: Dict[str, Any]) -> bool:
    if info.get("type") != "file":
        return False
    return Path(str(info.get("path") or info.get("name") or "")).name in _GOD_NODE_NOISY_FILES


_GAP_EXCLUDED_NAMES = frozenset({
    "__init__", "__post_init__", "__repr__", "__str__", "__eq__",
    "__hash__", "__lt__", "__le__", "__gt__", "__ge__",
    "upgrade", "main", "_main",
})

_GAP_EXCLUDED_PATH_SEGMENTS = frozenset({
    "migrations",
})

_GAP_REASON_LABELS = {
    "isolated_symbol": "Isolated",
    "only_contained": "Only contained",
}

_GAP_REASON_DESCRIPTIONS = {
    "isolated_symbol": "No graph edges reference this symbol.",
    "only_contained": "Only the containing file references this symbol.",
}


def _is_gap_noise(name: str, path: str) -> bool:
    bare = name.rsplit(".", 1)[-1] if "." in name else name
    if bare in _GAP_EXCLUDED_NAMES:
        return True
    if bare.startswith("_") and bare.endswith("_"):
        return True
    path_parts = path.split("/") if path else []
    if any(seg in _GAP_EXCLUDED_PATH_SEGMENTS for seg in path_parts):
        return True
    return False


def _gap_reason(degree: int) -> str:
    return "isolated_symbol" if degree == 0 else "only_contained"


def _knowledge_gaps(
    degree: Dict[str, int],
    node_info: Dict[str, Dict[str, Any]],
    limit: int = 10,
) -> List[Dict[str, Any]]:
    symbol_types = {"class", "function", "method", "test"}
    candidates: List[Tuple[str, int]] = []
    for node_id, info in sorted(node_info.items()):
        if info["type"] not in symbol_types:
            continue
        if _is_gap_noise(info["name"], info["path"]):
            continue
        deg = degree.get(node_id, 0)
        if deg <= 1:
            candidates.append((node_id, deg))

    candidates.sort(key=lambda kv: (kv[1], kv[0]))
    result: List[Dict[str, Any]] = []
    for node_id, deg in candidates[:limit]:
        info = node_info[node_id]
        reason = _gap_reason(deg)
        result.append({
            "node_id": node_id,
            "name": info["name"],
            "kind": info["type"],
            "path": info["path"],
            "degree": deg,
            "reason": reason,
            "reason_label": _GAP_REASON_LABELS[reason],
        })
    return result


def _knowledge_gap_groups(gaps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_reason: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for gap in gaps:
        by_reason[gap["reason"]].append(gap)

    result: List[Dict[str, Any]] = []
    for reason in ("isolated_symbol", "only_contained"):
        nodes = by_reason.get(reason)
        if not nodes:
            continue
        result.append({
            "reason": reason,
            "label": _GAP_REASON_LABELS[reason],
            "description": _GAP_REASON_DESCRIPTIONS[reason],
            "count": len(nodes),
            "examples": [node["name"] for node in nodes[:5]],
        })
    return result


def _suggested_questions(
    god_nodes: List[Dict[str, Any]],
    gaps: List[Dict[str, Any]],
    surprising: List[Dict[str, Any]],
) -> List[str]:
    questions: List[str] = []
    seen: Set[str] = set()

    def add(question: str) -> None:
        if question not in seen:
            seen.add(question)
            questions.append(question)

    for node in god_nodes[:3]:
        target = node["node_id"] or node["name"]
        add(
            f"Which callers, imports, and tests make `{node['name']}` a "
            f"{node['degree']}-edge hub? Run `csegraph inspect {target}` "
            "before changing its responsibilities."
        )
    for node in gaps[:3]:
        if node.get("reason") == "isolated_symbol":
            add(
                f"`{node['name']}` ({node['kind']}) has no graph edges. "
                "Should it be exported, called, tested, or removed?"
            )
        else:
            add(
                f"`{node['name']}` ({node['kind']}) is only connected to "
                "its containing file. What caller or test should prove it is still active?"
            )
    for edge in surprising[:2]:
        src_section = edge["source_path"].split("/")[0]
        tgt_section = edge["target_path"].split("/")[0]
        add(
            f"Does the `{src_section}` -> `{tgt_section}` dependency via "
            f"`{edge['relation']}` match the intended package layering?"
        )
    return questions
