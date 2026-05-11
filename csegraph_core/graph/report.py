"""Deterministic project report generated from the SQLite index."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from csegraph_core.core.models import ReportResult
from csegraph_core.index.repository import ProjectIndex


class ReportService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def report(self) -> ReportResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            project = index.get_project()
            project_id = int(project["id"])
            repo_root = project["root_dir"]

            node_counts = _node_counts(index, project_id)
            edge_counts = _edge_counts(index, project_id)
            parse_error_count = _parse_error_count(index, project_id)

            total_files = node_counts.get("file", 0)
            total_symbols = sum(
                node_counts.get(t, 0) for t in ("class", "function", "method", "test")
            )
            total_edges = sum(edge_counts.values())

            degree = _node_degrees(index, project_id)
            node_info = _node_info(index, project_id)
            god_nodes = _god_nodes(degree, node_info)
            knowledge_gaps = _knowledge_gaps(degree, node_info)
            surprising = _surprising_connections(index, project_id, node_info)
            sections = _sections(index, project_id, node_info, degree)
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
                surprising_connections=surprising,
                suggested_questions=questions,
                sections=sections,
            )
        finally:
            index.close()


def _node_counts(index: ProjectIndex, project_id: int) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in index.conn.execute(
        "SELECT type, COUNT(*) AS c FROM nodes WHERE project_id = ? GROUP BY type",
        (project_id,),
    ):
        counts[row["type"]] = row["c"]
    return counts


def _edge_counts(index: ProjectIndex, project_id: int) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in index.conn.execute(
        "SELECT relation, COUNT(*) AS c FROM edges WHERE project_id = ? GROUP BY relation",
        (project_id,),
    ):
        counts[row["relation"]] = row["c"]
    return counts


def _parse_error_count(index: ProjectIndex, project_id: int) -> int:
    row = index.conn.execute(
        "SELECT COUNT(*) AS c FROM nodes WHERE project_id = ? AND parse_status = 'error'",
        (project_id,),
    ).fetchone()
    return int(row["c"])


def _node_degrees(index: ProjectIndex, project_id: int) -> Dict[str, int]:
    degree: Dict[str, int] = Counter()
    for row in index.conn.execute(
        "SELECT source_node_id, target_node_id FROM edges WHERE project_id = ?",
        (project_id,),
    ):
        degree[row["source_node_id"]] += 1
        degree[row["target_node_id"]] += 1
    return dict(degree)


def _node_info(
    index: ProjectIndex, project_id: int
) -> Dict[str, Dict[str, Any]]:
    info: Dict[str, Dict[str, Any]] = {}
    for row in index.conn.execute(
        "SELECT id, type, name, path FROM nodes WHERE project_id = ?",
        (project_id,),
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
    for node_id, deg in ranked[:limit]:
        info = node_info.get(node_id, {})
        result.append({
            "node_id": node_id,
            "name": info.get("name", ""),
            "kind": info.get("type", ""),
            "path": info.get("path", ""),
            "degree": deg,
        })
    return result


_GAP_EXCLUDED_NAMES = frozenset({
    "__init__", "__post_init__", "__repr__", "__str__", "__eq__",
    "__hash__", "__lt__", "__le__", "__gt__", "__ge__",
    "upgrade", "main", "_main",
})

_GAP_EXCLUDED_PATH_SEGMENTS = frozenset({
    "migrations",
})


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
        result.append({
            "node_id": node_id,
            "name": info["name"],
            "kind": info["type"],
            "path": info["path"],
            "degree": deg,
        })
    return result


def _surprising_connections(
    index: ProjectIndex,
    project_id: int,
    node_info: Dict[str, Dict[str, Any]],
    limit: int = 10,
) -> List[Dict[str, Any]]:
    seen: Set[Tuple[str, str, str]] = set()
    result: List[Dict[str, Any]] = []
    for row in index.conn.execute(
        "SELECT source_node_id, target_node_id, relation FROM edges WHERE project_id = ?",
        (project_id,),
    ):
        src = row["source_node_id"]
        tgt = row["target_node_id"]
        key = (src, row["relation"], tgt)
        if key in seen:
            continue
        seen.add(key)
        src_info = node_info.get(src, {})
        tgt_info = node_info.get(tgt, {})
        src_path = src_info.get("path", "")
        tgt_path = tgt_info.get("path", "")
        if not src_path or not tgt_path or src_path == tgt_path:
            continue
        src_pkg = src_path.split("/")[0] if "/" in src_path else ""
        tgt_pkg = tgt_path.split("/")[0] if "/" in tgt_path else ""
        if src_pkg and tgt_pkg and src_pkg != tgt_pkg:
            result.append({
                "source": src,
                "target": tgt,
                "relation": row["relation"],
                "source_path": src_path,
                "target_path": tgt_path,
            })
    result.sort(key=lambda e: (e["source"], e["relation"], e["target"]))
    return result[:limit]


def _sections(
    index: ProjectIndex,
    project_id: int,
    node_info: Dict[str, Dict[str, Any]],
    degree: Dict[str, int],
) -> List[Dict[str, Any]]:
    section_files: Dict[str, int] = Counter()
    section_symbols: Dict[str, int] = Counter()
    node_section: Dict[str, str] = {}
    symbol_types = {"class", "function", "method", "test"}

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

    section_internal: Dict[str, int] = Counter()
    section_cross: Dict[str, Set[str]] = defaultdict(set)
    for row in index.conn.execute(
        "SELECT source_node_id, target_node_id FROM edges WHERE project_id = ?",
        (project_id,),
    ):
        src_sec = node_section.get(row["source_node_id"])
        tgt_sec = node_section.get(row["target_node_id"])
        if src_sec is None or tgt_sec is None:
            continue
        if src_sec == tgt_sec:
            section_internal[src_sec] += 1
        else:
            section_cross[src_sec].add(tgt_sec)
            section_cross[tgt_sec].add(src_sec)

    all_sections = sorted(set(section_files) | set(section_symbols))
    result: List[Dict[str, Any]] = []
    for section in all_sections:
        result.append({
            "name": section,
            "files": section_files.get(section, 0),
            "symbols": section_symbols.get(section, 0),
            "internal_edges": section_internal.get(section, 0),
            "cross_section_deps": sorted(section_cross.get(section, set())),
        })
    return result


def _suggested_questions(
    god_nodes: List[Dict[str, Any]],
    gaps: List[Dict[str, Any]],
    surprising: List[Dict[str, Any]],
) -> List[str]:
    questions: List[str] = []
    for node in god_nodes[:3]:
        questions.append(
            f"Why does `{node['name']}` have {node['degree']} connections? "
            f"Could it be decomposed?"
        )
    for node in gaps[:3]:
        if node["degree"] == 0:
            questions.append(
                f"`{node['name']}` ({node['kind']}) is completely isolated. "
                f"Is it dead code?"
            )
        else:
            questions.append(
                f"`{node['name']}` ({node['kind']}) has only {node['degree']} "
                f"connection. Is it under-tested or under-used?"
            )
    for edge in surprising[:2]:
        questions.append(
            f"`{edge['source_path']}` {edge['relation']} `{edge['target_path']}`. "
            f"Is this cross-package dependency intentional?"
        )
    return questions
