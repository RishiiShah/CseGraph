"""Leiden community detection on the csegraph dependency graph.

Uses a pure-Python Louvain/Leiden-style greedy modularity optimization.
No external dependencies required.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from csegraph_core.index.loaders import load_edges, load_nodes, SYMBOL_TYPES
from csegraph_core.index.repository import ProjectIndex


@dataclass
class Community:
    id: int
    node_ids: List[str]
    size: int
    label: str


@dataclass
class CommunityResult:
    command: str
    db_path: str
    repo_root: str
    num_communities: int
    modularity: float
    communities: List[Community]


_RELATION_WEIGHTS = {
    "calls": 2.5,
    "inherits": 1.5,
    "tested_by": 1.0,
    "imports": 0.8,
    "decorates": 0.6,
    "contains": 0.2,
}


def _build_weighted_graph(
    edges: List[Dict[str, Any]],
    valid_nodes: Set[str],
) -> Tuple[Dict[str, Dict[str, float]], float]:
    adj: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    total_weight = 0.0
    for edge in edges:
        s, t = edge["source_id"], edge["target_id"]
        if s not in valid_nodes or t not in valid_nodes:
            continue
        if s == t:
            continue
        w = _RELATION_WEIGHTS.get(edge["relation"], 0.3)
        adj[s][t] += w
        adj[t][s] += w
        total_weight += w
    return dict(adj), total_weight


def _modularity(
    adj: Dict[str, Dict[str, float]],
    communities: Dict[str, int],
    total_weight: float,
) -> float:
    if total_weight == 0:
        return 0.0
    m2 = 2.0 * total_weight
    q = 0.0
    strength: Dict[str, float] = {}
    for node, neighbors in adj.items():
        strength[node] = sum(neighbors.values())

    comm_internal: Dict[int, float] = defaultdict(float)
    comm_total: Dict[int, float] = defaultdict(float)

    for node, neighbors in adj.items():
        c = communities[node]
        comm_total[c] += strength.get(node, 0.0)
        for neighbor, w in neighbors.items():
            if communities[neighbor] == c:
                comm_internal[c] += w

    for c in comm_internal:
        comm_internal[c] /= 2.0

    for c in set(communities.values()):
        ec = comm_internal.get(c, 0.0)
        ac = comm_total.get(c, 0.0)
        q += ec / total_weight - (ac / m2) ** 2

    return q


def _louvain_pass(
    nodes: List[str],
    adj: Dict[str, Dict[str, float]],
    total_weight: float,
) -> Dict[str, int]:
    community: Dict[str, int] = {n: i for i, n in enumerate(nodes)}

    strength: Dict[str, float] = {}
    for n in nodes:
        strength[n] = sum(adj.get(n, {}).values())

    comm_totals: Dict[int, float] = defaultdict(float)
    for n in nodes:
        comm_totals[community[n]] += strength.get(n, 0.0)

    m2 = 2.0 * total_weight if total_weight > 0 else 1.0
    improved = True

    while improved:
        improved = False
        for node in nodes:
            if node not in adj:
                continue
            current_comm = community[node]
            ki = strength.get(node, 0.0)

            neighbor_comms: Dict[int, float] = defaultdict(float)
            for neighbor, w in adj[node].items():
                neighbor_comms[community[neighbor]] += w

            best_comm = current_comm
            best_gain = 0.0

            remove_cost = neighbor_comms.get(current_comm, 0.0)
            sigma_current = comm_totals[current_comm] - ki

            for c, ki_in in neighbor_comms.items():
                if c == current_comm:
                    continue
                sigma_c = comm_totals[c]
                gain = (ki_in - remove_cost) / total_weight - ki * (sigma_c - sigma_current) / (m2 * total_weight) if total_weight > 0 else 0
                if gain > best_gain:
                    best_gain = gain
                    best_comm = c

            if best_comm != current_comm:
                comm_totals[current_comm] -= ki
                comm_totals[best_comm] += ki
                community[node] = best_comm
                improved = True

    label_map: Dict[int, int] = {}
    counter = 0
    result: Dict[str, int] = {}
    for n in nodes:
        old = community[n]
        if old not in label_map:
            label_map[old] = counter
            counter += 1
        result[n] = label_map[old]
    return result


def detect_communities(db_path: str | Path) -> CommunityResult:
    db = str(Path(db_path))
    index = ProjectIndex(db)
    try:
        index.initialize_schema()
        metadata = index.metadata()
        repo_root = metadata["root_dir"]

        all_nodes = load_nodes(index, types=("file", *SYMBOL_TYPES))
        edges = load_edges(index)

        node_ids = list(all_nodes.keys())
        valid = set(node_ids)

        if not node_ids:
            return CommunityResult(
                command="communities",
                db_path=db,
                repo_root=repo_root,
                num_communities=0,
                modularity=0.0,
                communities=[],
            )

        adj, total_weight = _build_weighted_graph(edges, valid)
        assignments = _louvain_pass(node_ids, adj, total_weight)
        mod = _modularity(adj, assignments, total_weight)

        groups: Dict[int, List[str]] = defaultdict(list)
        for node_id, comm_id in assignments.items():
            groups[comm_id].append(node_id)

        index.conn.executemany(
            "UPDATE nodes SET community_id = ? WHERE id = ?",
            [(comm_id, node_id) for node_id, comm_id in assignments.items()],
        )
        index.conn.commit()

        communities = []
        for comm_id, members in sorted(groups.items()):
            names = [all_nodes[m].get("name", "") for m in members[:3]]
            label = ", ".join(n for n in names if n)
            communities.append(Community(
                id=comm_id,
                node_ids=sorted(members),
                size=len(members),
                label=label,
            ))

        communities.sort(key=lambda c: c.size, reverse=True)

        return CommunityResult(
            command="communities",
            db_path=db,
            repo_root=repo_root,
            num_communities=len(communities),
            modularity=round(mod, 4),
            communities=communities,
        )
    finally:
        index.close()
