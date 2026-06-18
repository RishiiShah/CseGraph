"""Community summaries and architecture overview from the csegraph index."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from csegraph._core.core.models import (
    ArchitectureResult,
    CommunitySummary,
    CouplingPair,
)
from csegraph._core.index.repository import ProjectIndex


class ArchitectureService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def overview(self, *, limit: int = 20) -> ArchitectureResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]

            communities = _load_community_nodes(index)
            if not communities:
                return ArchitectureResult(
                    command="architecture",
                    db_path=self.db_path,
                    repo_root=repo_root,
                    total_nodes=0,
                    total_edges=0,
                    num_communities=0,
                    summaries=[],
                    coupling=[],
                    warnings=["No communities detected. Run `csegraph postprocess` first."],
                )

            node_info = _node_info_map(index)
            edges = _load_all_edges(index)

            total_nodes = len(node_info)
            total_edges = len(edges)

            summaries = _build_summaries(communities, node_info, edges)
            summaries.sort(key=lambda s: s.size, reverse=True)
            summaries = summaries[:limit]

            coupling = _build_coupling(communities, node_info, edges)

            warnings: List[str] = []
            high_coupling = [c for c in coupling if c.weight >= 10]
            for cp in high_coupling[:3]:
                warnings.append(
                    f"High coupling ({cp.weight} edges) between "
                    f"community {cp.community_a} ({cp.label_a}) and "
                    f"community {cp.community_b} ({cp.label_b})."
                )

            return ArchitectureResult(
                command="architecture",
                db_path=self.db_path,
                repo_root=repo_root,
                total_nodes=total_nodes,
                total_edges=total_edges,
                num_communities=len(communities),
                summaries=summaries,
                coupling=coupling,
                warnings=warnings,
            )
        finally:
            index.close()


def _load_community_nodes(index: ProjectIndex) -> Dict[int, List[str]]:
    groups: Dict[int, List[str]] = defaultdict(list)
    for row in index.conn.execute(
        "SELECT id, community_id FROM nodes WHERE community_id IS NOT NULL"
    ):
        groups[row["community_id"]].append(row["id"])
    return dict(groups)


def _node_info_map(index: ProjectIndex) -> Dict[str, Dict[str, Any]]:
    info: Dict[str, Dict[str, Any]] = {}
    for row in index.conn.execute(
        "SELECT id, type, name, path, language, community_id, is_test FROM nodes"
    ):
        info[row["id"]] = dict(row)
    return info


def _load_all_edges(index: ProjectIndex) -> List[Dict[str, Any]]:
    return [dict(row) for row in index.conn.execute("SELECT source, target, relation FROM edges")]


def _community_label(members: List[str], node_info: Dict[str, Dict[str, Any]]) -> str:
    dir_counts: Counter = Counter()
    for nid in members:
        info = node_info.get(nid, {})
        path = info.get("path", "")
        if not path:
            continue
        parts = path.replace("\\", "/").split("/")
        if len(parts) > 1:
            dir_counts[parts[0]] += 1
        else:
            dir_counts["(root)"] += 1

    if not dir_counts:
        symbol_names = [
            node_info[m]["name"] for m in members[:3] if node_info.get(m, {}).get("name")
        ]
        return ", ".join(symbol_names) or "unnamed"

    top_dir = dir_counts.most_common(1)[0][0]
    symbol_types = {"class", "function", "method", "test"}
    symbols_in_dir = [
        node_info[m]["name"]
        for m in members
        if node_info.get(m, {}).get("type") in symbol_types
        and node_info.get(m, {}).get("path", "").replace("\\", "/").startswith(top_dir)
    ]
    if symbols_in_dir:
        return (
            f"{top_dir} ({symbols_in_dir[0]}...)"
            if len(symbols_in_dir) > 1
            else f"{top_dir} ({symbols_in_dir[0]})"
        )
    return top_dir


def _build_summaries(
    communities: Dict[int, List[str]],
    node_info: Dict[str, Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> List[CommunitySummary]:
    node_to_comm: Dict[str, int] = {}
    for comm_id, members in communities.items():
        for nid in members:
            node_to_comm[nid] = comm_id

    comm_internal: Counter = Counter()
    comm_cross: Counter = Counter()
    for edge in edges:
        src_c = node_to_comm.get(edge["source"])
        tgt_c = node_to_comm.get(edge["target"])
        if src_c is None or tgt_c is None:
            continue
        if src_c == tgt_c:
            comm_internal[src_c] += 1
        else:
            comm_cross[src_c] += 1
            comm_cross[tgt_c] += 1

    degree: Counter = Counter()
    for edge in edges:
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1

    summaries: List[CommunitySummary] = []
    for comm_id, members in communities.items():
        symbol_types = {"class", "function", "method", "test"}
        languages: Counter = Counter()
        files: Set[str] = set()
        type_counts: Counter = Counter()
        test_count = 0

        for nid in members:
            info = node_info.get(nid, {})
            ntype = info.get("type", "")
            type_counts[ntype] += 1
            lang = info.get("language")
            if lang:
                languages[lang] += 1
            path = info.get("path", "")
            if path:
                files.add(path)
            if info.get("is_test"):
                test_count += 1

        key_symbols = sorted(
            [nid for nid in members if node_info.get(nid, {}).get("type") in symbol_types],
            key=lambda x: -degree.get(x, 0),
        )[:5]

        key_symbol_names = [
            {
                "id": nid,
                "name": node_info[nid]["name"],
                "kind": node_info[nid]["type"],
                "degree": degree.get(nid, 0),
            }
            for nid in key_symbols
            if nid in node_info
        ]

        label = _community_label(members, node_info)

        summaries.append(
            CommunitySummary(
                community_id=comm_id,
                label=label,
                size=len(members),
                files=len(files),
                languages=dict(languages.most_common()),
                type_counts=dict(type_counts.most_common()),
                key_symbols=key_symbol_names,
                internal_edges=comm_internal.get(comm_id, 0),
                cross_edges=comm_cross.get(comm_id, 0),
                test_count=test_count,
            )
        )

    return summaries


def _build_coupling(
    communities: Dict[int, List[str]],
    node_info: Dict[str, Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> List[CouplingPair]:
    node_to_comm: Dict[str, int] = {}
    for comm_id, members in communities.items():
        for nid in members:
            node_to_comm[nid] = comm_id

    pair_counts: Counter = Counter()
    pair_relations: Dict[Tuple[int, int], Counter] = defaultdict(Counter)

    for edge in edges:
        src_c = node_to_comm.get(edge["source"])
        tgt_c = node_to_comm.get(edge["target"])
        if src_c is None or tgt_c is None or src_c == tgt_c:
            continue
        key = (min(src_c, tgt_c), max(src_c, tgt_c))
        pair_counts[key] += 1
        pair_relations[key][edge["relation"]] += 1

    labels: Dict[int, str] = {}
    for comm_id, members in communities.items():
        labels[comm_id] = _community_label(members, node_info)

    coupling: List[CouplingPair] = []
    for (a, b), weight in pair_counts.most_common():
        coupling.append(
            CouplingPair(
                community_a=a,
                community_b=b,
                label_a=labels.get(a, ""),
                label_b=labels.get(b, ""),
                weight=weight,
                relations=dict(pair_relations[(a, b)].most_common()),
            )
        )

    return coupling
