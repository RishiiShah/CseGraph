from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

from csegraph._core.core.models import (
    GraphEdgeView,
    GraphNodeView,
    GraphResult,
    PathEdge,
    PathResult,
    PathStep,
)
from csegraph._core.index.repository import ProjectIndex, json_loads

_MINIMAL_GRAPH_KEY_NODES = 5
_HUB_FLOOR = 50
_HUB_PERCENTILE = 0.99

# Single-threaded: MCP stdio server runs one asyncio event loop, no concurrent
# tool calls. If the server ever moves to a threaded transport, add a lock.
_hub_cache: Dict[Tuple[str, int, FrozenSet[str]], Tuple[int, FrozenSet[str]]] = {}
_HUB_CACHE_MAX = 32


def clear_hub_cache() -> None:
    _hub_cache.clear()


def _relation_clause(relations: Optional[List[str]]) -> Tuple[str, Tuple[Any, ...]]:
    """Return (sql_fragment, params). Fragment starts with 'WHERE' or is empty."""
    if not relations:
        return "", ()
    placeholders = ",".join("?" for _ in relations)
    return f"WHERE relation IN ({placeholders})", tuple(relations)


def _compute_hub_threshold(index: Any, relations: Optional[List[str]] = None) -> int:
    """p99 of node degrees (optionally restricted to given relations), floored at _HUB_FLOOR."""
    clause, params = _relation_clause(relations)
    rows = index.conn.execute(
        f"""
        SELECT COUNT(*) AS deg FROM (
            SELECT source AS node_id FROM edges {clause}
            UNION ALL
            SELECT target AS node_id FROM edges {clause}
        )
        GROUP BY node_id
        ORDER BY deg ASC
        """,
        params + params,
    ).fetchall()
    if not rows:
        return _HUB_FLOOR
    degrees = [int(row["deg"]) for row in rows]
    idx = max(0, math.ceil(_HUB_PERCENTILE * len(degrees)) - 1)
    p99 = degrees[idx]
    return max(p99, _HUB_FLOOR)


def _hub_node_ids(
    index: Any,
    threshold: int,
    relations: Optional[List[str]] = None,
) -> Set[str]:
    clause, rel_params = _relation_clause(relations)
    rows = index.conn.execute(
        f"""
        SELECT node_id FROM (
            SELECT source AS node_id FROM edges {clause}
            UNION ALL
            SELECT target AS node_id FROM edges {clause}
        )
        GROUP BY node_id
        HAVING COUNT(*) >= ?
        """,
        rel_params + rel_params + (threshold,),
    ).fetchall()
    return {row["node_id"] for row in rows}


VALID_CONFIDENCE_TIERS = frozenset({"EXTRACTED", "INFERRED", "AMBIGUOUS"})


def _cached_hub_info(
    db_path: str, index: Any, data_version: int, relations: Optional[List[str]] = None
) -> Tuple[int, Set[str]]:
    key = (db_path, data_version, frozenset(relations or ()))
    cached = _hub_cache.get(key)
    if cached is not None:
        return cached[0], set(cached[1])
    if len(_hub_cache) >= _HUB_CACHE_MAX:
        _hub_cache.clear()
    threshold = _compute_hub_threshold(index, relations)
    hubs = _hub_node_ids(index, threshold, relations)
    _hub_cache[key] = (threshold, frozenset(hubs))
    return threshold, hubs


class GraphQueryService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def neighborhood(
        self,
        node_id: str,
        depth: int = 1,
        detail_level: str = "minimal",
        relations: Optional[List[str]] = None,
        confidence_tiers: Optional[List[str]] = None,
    ) -> GraphResult:
        if detail_level not in ("minimal", "standard"):
            raise ValueError(f"detail_level must be 'minimal' or 'standard', got '{detail_level}'")
        relations_filter = [r for r in (relations or []) if r]
        tier_filter = [t for t in (confidence_tiers or []) if t]
        if tier_filter:
            unknown = set(tier_filter) - VALID_CONFIDENCE_TIERS
            if unknown:
                raise ValueError(
                    f"Unknown confidence_tiers: {sorted(unknown)}. Valid: {sorted(VALID_CONFIDENCE_TIERS)}"
                )
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]

            from csegraph._core.retrieval.cache import CACHE

            snapshot = CACHE.get_snapshot(index)

            resolved = _resolve_graph_node(index, node_id, repo_root)

            _, hubs = _cached_hub_info(self.db_path, index, snapshot.data_version, relations_filter)
            hubs = set(hubs)
            hubs.discard(resolved)

            visited: Set[str] = set()
            queue = [(resolved, 0)]

            while queue:
                curr, d = queue.pop(0)
                if curr in visited:
                    continue
                visited.add(curr)

                # Do not expand from hubs (except the resolved target itself)
                if curr in hubs and curr != resolved:
                    continue

                if d < depth:
                    for edge in snapshot.outgoing.get(curr, []):
                        if relations_filter and edge["relation"] not in relations_filter:
                            continue
                        if tier_filter and edge["confidence_tier"] not in tier_filter:
                            continue
                        nxt = edge["target"]
                        if nxt not in visited:
                            queue.append((nxt, d + 1))
                    for edge in snapshot.incoming.get(curr, []):
                        if relations_filter and edge["relation"] not in relations_filter:
                            continue
                        if tier_filter and edge["confidence_tier"] not in tier_filter:
                            continue
                        nxt = edge["source"]
                        if nxt not in visited:
                            queue.append((nxt, d + 1))

            hubs_skipped = len(hubs & visited)
            visited_list = list(visited)

            node_rows: Dict[str, Dict[str, Any]] = {}
            for nid in visited_list:
                if nid in snapshot.node_rows_light:
                    node_rows[nid] = snapshot.node_rows_light[nid]
                elif nid in snapshot.files:
                    node_rows[nid] = snapshot.files[nid]

            selected_edges: Dict[tuple, Dict[str, Any]] = {}
            for nid in visited_list:
                for edge in snapshot.outgoing.get(nid, []):
                    if edge["target"] in visited:
                        if relations_filter and edge["relation"] not in relations_filter:
                            continue
                        if tier_filter and edge["confidence_tier"] not in tier_filter:
                            continue
                        key = (
                            edge["source"],
                            edge["target"],
                            edge["relation"],
                            edge.get("metadata") or "",
                        )
                        selected_edges[key] = edge

            total_nodes = len(visited)
            total_edges = len(selected_edges)
            confidence_breakdown = _confidence_breakdown(selected_edges.values())
            hubs_note = f" Skipped {hubs_skipped} hub(s)." if hubs_skipped else ""
            conf_note = _confidence_note(confidence_breakdown)
            summary = (
                f"{total_nodes} nodes, {total_edges} edges within depth {depth} around "
                f"'{_short_name(resolved, node_rows)}'." + hubs_note + conf_note
            )

            if detail_level == "minimal":
                key_node_ids = _top_nodes_by_degree(
                    visited_list,
                    selected_edges.values(),
                    resolved,
                    limit=_MINIMAL_GRAPH_KEY_NODES,
                )
                nodes = [_node_view_from_row(nid, node_rows) for nid in key_node_ids]
                return GraphResult(
                    command="graph",
                    db_path=self.db_path,
                    repo_root=repo_root,
                    target=resolved,
                    depth=depth,
                    nodes=nodes,
                    edges=[],
                    detail_level="minimal",
                    summary=summary,
                    total_nodes=total_nodes,
                    total_edges=total_edges,
                    truncated=total_nodes > len(nodes) or total_edges > 0,
                    hubs_skipped=hubs_skipped,
                    relations_filter=list(relations_filter),
                    confidence_breakdown=confidence_breakdown,
                )

            nodes = [_node_view_from_row(nid, node_rows) for nid in sorted(visited)]
            graph_edges = [
                GraphEdgeView(
                    source=edge["source"],
                    target=edge["target"],
                    relation=edge["relation"],
                    metadata=json_loads(edge.get("metadata")),
                    confidence=float(edge.get("confidence", 1.0)),
                    confidence_tier=edge.get("confidence_tier") or "EXTRACTED",
                )
                for edge in sorted(
                    selected_edges.values(),
                    key=lambda e: (e["source"], e["relation"], e["target"]),
                )
            ]
            return GraphResult(
                command="graph",
                db_path=self.db_path,
                repo_root=repo_root,
                target=resolved,
                depth=depth,
                nodes=nodes,
                edges=graph_edges,
                detail_level="standard",
                summary=summary,
                total_nodes=total_nodes,
                total_edges=total_edges,
                truncated=False,
                hubs_skipped=hubs_skipped,
                relations_filter=list(relations_filter),
                confidence_breakdown=confidence_breakdown,
            )
        finally:
            index.close()

    def shortest_path(
        self,
        source: str,
        target: str,
        detail_level: str = "minimal",
        relations: Optional[List[str]] = None,
        confidence_tiers: Optional[List[str]] = None,
        max_depth: int = 15,
    ) -> PathResult:
        if detail_level not in ("minimal", "standard"):
            raise ValueError(f"detail_level must be 'minimal' or 'standard', got '{detail_level}'")
        relations_filter = [r for r in (relations or []) if r] or None
        tier_filter = [t for t in (confidence_tiers or []) if t]
        if tier_filter:
            unknown = set(tier_filter) - VALID_CONFIDENCE_TIERS
            if unknown:
                raise ValueError(
                    f"Unknown confidence_tiers: {sorted(unknown)}. Valid: {sorted(VALID_CONFIDENCE_TIERS)}"
                )
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]

            from csegraph._core.retrieval.cache import CACHE

            snapshot = CACHE.get_snapshot(index)

            src = _resolve_graph_node(index, source, repo_root)
            dst = _resolve_graph_node(index, target, repo_root)

            _, hubs = _cached_hub_info(self.db_path, index, snapshot.data_version, relations_filter)
            hubs = set(hubs)
            hubs.discard(src)
            hubs.discard(dst)

            parent_map: Dict[str, Tuple[str, str, str]] = {}
            visited: Set[str] = set()
            queue: List[Tuple[str, int, Optional[str], Optional[str], Optional[str]]] = [
                (src, 0, None, None, None)
            ]

            while queue:
                curr, d, p_node, p_rel, p_tier = queue.pop(0)
                if curr in visited:
                    continue
                visited.add(curr)
                if curr not in parent_map and p_node is not None:
                    assert p_rel is not None
                    assert p_tier is not None
                    parent_map[curr] = (p_node, p_rel, p_tier)

                if curr == dst:
                    break

                # Do not expand from hubs (except src or dst)
                if curr in hubs and curr != src and curr != dst:
                    continue

                if d < max_depth:
                    for edge in snapshot.outgoing.get(curr, []):
                        if relations_filter and edge["relation"] not in relations_filter:
                            continue
                        if tier_filter and edge["confidence_tier"] not in tier_filter:
                            continue
                        nxt = edge["target"]
                        if nxt not in visited:
                            queue.append(
                                (
                                    nxt,
                                    d + 1,
                                    curr,
                                    edge["relation"],
                                    edge.get("confidence_tier", "EXTRACTED"),
                                )
                            )

                    for edge in snapshot.incoming.get(curr, []):
                        if relations_filter and edge["relation"] not in relations_filter:
                            continue
                        if tier_filter and edge["confidence_tier"] not in tier_filter:
                            continue
                        nxt = edge["source"]
                        if nxt not in visited:
                            queue.append(
                                (
                                    nxt,
                                    d + 1,
                                    curr,
                                    edge["relation"],
                                    edge.get("confidence_tier", "EXTRACTED"),
                                )
                            )

            if dst not in parent_map and dst != src:
                summary = f"No path: '{source}' ↛ '{target}'."
                return PathResult(
                    command="path",
                    db_path=self.db_path,
                    repo_root=repo_root,
                    source=src,
                    target=dst,
                    found=False,
                    length=0,
                    nodes=[],
                    edges=[],
                    detail_level=detail_level,
                    summary=summary,
                )

            # Reconstruct path from dst back to src.
            path_ids: List[str] = []
            path_edges: List[PathEdge] = []
            confidence_counts: Dict[str, int] = {}
            visited_trace: Set[str] = set()
            node: Optional[str] = dst
            while node is not None:
                if node in visited_trace:
                    break
                visited_trace.add(node)
                path_ids.append(node)
                entry = parent_map.get(node)
                if entry is not None:
                    parent, relation, tier = entry
                    path_edges.append(PathEdge(source=parent, target=node, relation=relation))
                    confidence_counts[tier] = confidence_counts.get(tier, 0) + 1
                    node = parent
                else:
                    node = None
            path_ids.reverse()
            path_edges.reverse()

            node_rows: Dict[str, Dict[str, Any]] = {}
            for nid in path_ids:
                if nid in snapshot.node_rows_light:
                    node_rows[nid] = snapshot.node_rows_light[nid]
                elif nid in snapshot.files:
                    node_rows[nid] = snapshot.files[nid]

            name_chain = " → ".join(_short_name(nid, node_rows) for nid in path_ids)
            summary = f"{name_chain} ({len(path_edges)} hops)"

            hubs_skipped = sum(1 for nid in path_ids if nid in hubs)

            if detail_level == "minimal":
                minimal_steps = [
                    PathStep(
                        node_id=nid,
                        kind="",
                        name=_short_name(nid, node_rows),
                        path=str(node_rows.get(nid, {}).get("path") or ""),
                        line_range=None,
                    )
                    for nid in path_ids
                ]
                return PathResult(
                    command="path",
                    db_path=self.db_path,
                    repo_root=repo_root,
                    source=src,
                    target=dst,
                    found=True,
                    length=len(path_edges),
                    nodes=minimal_steps,
                    edges=[],
                    detail_level="minimal",
                    summary=summary,
                    relations_filter=list(relations or []),
                    hubs_skipped=hubs_skipped,
                    confidence_breakdown=confidence_counts,
                )

            steps = [_path_step_from_row(nid, node_rows) for nid in path_ids]
            return PathResult(
                command="path",
                db_path=self.db_path,
                repo_root=repo_root,
                source=src,
                target=dst,
                found=True,
                length=len(path_edges),
                nodes=steps,
                edges=path_edges,
                detail_level="standard",
                summary=summary,
                relations_filter=list(relations or []),
                hubs_skipped=hubs_skipped,
                confidence_breakdown=confidence_counts,
            )
        finally:
            index.close()


def _confidence_breakdown(edges: Any) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for edge in edges:
        tier = edge.get("confidence_tier") or "EXTRACTED"
        counts[tier] = counts.get(tier, 0) + 1
    return counts


def _confidence_note(breakdown: Dict[str, int]) -> str:
    extras = [
        f"{count} {tier.lower()}"
        for tier, count in breakdown.items()
        if tier != "EXTRACTED" and count > 0
    ]
    return f" Confidence: {', '.join(extras)}." if extras else ""


def _short_name(node_id: str, node_rows: Dict[str, Dict[str, Any]]) -> str:
    row = node_rows.get(node_id)
    if row is None:
        return node_id
    name = row.get("name")
    if name:
        return name
    path = row.get("path") or ""
    return Path(path).name if path else node_id


def _top_nodes_by_degree(
    visited: List[str],
    edges: Any,
    target_id: str,
    limit: int,
) -> List[str]:
    degree: Dict[str, int] = {nid: 0 for nid in visited}
    for edge in edges:
        if edge["source"] in degree:
            degree[edge["source"]] += 1
        if edge["target"] in degree:
            degree[edge["target"]] += 1
    ranked = sorted(
        visited,
        key=lambda nid: (nid != target_id, -degree.get(nid, 0), nid),
    )
    return ranked[:limit]


def _path_step_from_row(node_id: str, node_rows: Dict[str, Dict[str, Any]]) -> PathStep:
    row = node_rows.get(node_id)
    if row is None:
        return PathStep(
            node_id=node_id,
            kind="external",
            name=node_id,
            path="",
        )
    ntype = row["type"]
    start = row.get("start_line")
    end = row.get("end_line")
    line_range = [int(start), int(end)] if start is not None and end is not None else None
    return PathStep(
        node_id=node_id,
        kind=ntype,
        name=row.get("name", ""),
        path=row.get("path", ""),
        line_range=line_range,
    )


def _resolve_graph_node(
    index: ProjectIndex,
    node: str,
    repo_root: str = "",
) -> str:
    row = index.conn.execute("SELECT id FROM nodes WHERE id = ?", (node,)).fetchone()
    if row:
        return row["id"]

    if node == ".":
        repo_row = index.conn.execute("SELECT id FROM nodes WHERE type = 'repo' LIMIT 1").fetchone()
        if repo_row:
            return repo_row["id"]

    if not repo_root:
        try:
            metadata = index.metadata()
            repo_root = metadata.get("root_dir", "")
        except Exception:
            logger.debug("metadata fetch failed in _resolve_graph_node", exc_info=True)

    repo_basename = Path(repo_root).name if repo_root else ""
    if repo_basename and node == repo_basename:
        repo_row = index.conn.execute("SELECT id FROM nodes WHERE type = 'repo' LIMIT 1").fetchone()
        if repo_row:
            return repo_row["id"]

    if repo_root:
        try:
            resolved_path = Path(node).resolve()
            resolved_root = Path(repo_root).resolve()
            if resolved_path == resolved_root:
                repo_row = index.conn.execute(
                    "SELECT id FROM nodes WHERE type = 'repo' LIMIT 1"
                ).fetchone()
                if repo_row:
                    return repo_row["id"]
            elif resolved_path.is_relative_to(resolved_root):
                rel_path = resolved_path.relative_to(resolved_root).as_posix()
                row = index.conn.execute(
                    "SELECT id FROM nodes WHERE type = 'file' AND LOWER(path) = ? LIMIT 1",
                    (rel_path.lower(),),
                ).fetchone()
                if row:
                    return row["id"]
        except Exception:
            logger.debug("path resolution failed in _resolve_graph_node", exc_info=True)

    lowered = node.lower()
    row = index.conn.execute(
        """
        SELECT id FROM nodes
        WHERE type IN ('class','function','method','test')
          AND LOWER(name) = ?
        ORDER BY length(name) ASC
        LIMIT 1
        """,
        (lowered,),
    ).fetchone()
    if row:
        return row["id"]

    row = index.conn.execute(
        "SELECT id FROM nodes WHERE type = 'file' AND LOWER(path) = ? LIMIT 1",
        (lowered,),
    ).fetchone()
    if row:
        return row["id"]

    row = index.conn.execute(
        """
        SELECT id FROM nodes
        WHERE type IN ('repo','folder')
          AND (LOWER(name) = ? OR LOWER(path) = ?)
        LIMIT 1
        """,
        (lowered, lowered),
    ).fetchone()
    if row:
        return row["id"]

    raise ValueError(f"Node '{node}' did not match any indexed file, symbol, or folder.")


def _node_view_from_row(
    node_id: str,
    node_rows: Dict[str, Dict[str, Any]],
) -> GraphNodeView:
    row = node_rows.get(node_id)
    if row is None:
        return GraphNodeView(
            id=node_id,
            kind="external",
            name=node_id,
            path="",
        )
    ntype = row["type"]
    if ntype in ("class", "function", "method", "test"):
        start = row.get("start_line")
        end = row.get("end_line")
        line_range = [int(start), int(end)] if start is not None and end is not None else None
        return GraphNodeView(
            id=node_id,
            kind=ntype,
            name=row["name"],
            path=row["path"],
            line_range=line_range,
        )
    if ntype == "file":
        return GraphNodeView(
            id=node_id,
            kind="file",
            name=Path(row["path"]).name,
            path=row["path"],
        )
    return GraphNodeView(
        id=node_id,
        kind=ntype,
        name=row.get("name", ""),
        path=row.get("path", ""),
    )
