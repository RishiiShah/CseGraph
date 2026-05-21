from __future__ import annotations

import math
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from csegraph_core.core.models import (
    GraphEdgeView,
    GraphNodeView,
    GraphResult,
    PathEdge,
    PathResult,
    PathStep,
)
from csegraph_core.index.repository import ProjectIndex, json_loads


_MINIMAL_GRAPH_KEY_NODES = 5
_HUB_FLOOR = 50
_HUB_PERCENTILE = 0.99


_BFS_CTE = """
WITH RECURSIVE bfs(node_id, depth) AS (
    SELECT ?, 0
  UNION
    SELECT
        CASE WHEN e.source = bfs.node_id THEN e.target
             ELSE e.source END,
        bfs.depth + 1
    FROM bfs
    JOIN edges e
      ON (e.source = bfs.node_id OR e.target = bfs.node_id)
    WHERE bfs.depth < ?
)
SELECT DISTINCT node_id FROM bfs
"""


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


class GraphQueryService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def neighborhood(
        self,
        node_id: str,
        depth: int = 1,
        detail_level: str = "minimal",
        relations: Optional[List[str]] = None,
    ) -> GraphResult:
        if detail_level not in ("minimal", "standard"):
            raise ValueError(f"detail_level must be 'minimal' or 'standard', got '{detail_level}'")
        relations_filter = [r for r in (relations or []) if r]
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]

            resolved = _resolve_graph_node(index, node_id, repo_root)

            threshold = _compute_hub_threshold(index, relations_filter)
            hubs = _hub_node_ids(index, threshold, relations_filter)
            hubs.discard(resolved)

            bfs_rel_clause = (
                f"AND e.relation IN ({','.join('?' for _ in relations_filter)})"
                if relations_filter
                else ""
            )

            visited: Set[str] = set()
            if hubs:
                hub_placeholders = ",".join("?" for _ in hubs)
                hub_aware_cte = f"""
                WITH RECURSIVE bfs(node_id, depth) AS (
                    SELECT ?, 0
                  UNION
                    SELECT
                        CASE WHEN e.source = bfs.node_id THEN e.target
                             ELSE e.source END,
                        bfs.depth + 1
                    FROM bfs
                    JOIN edges e
                      ON (e.source = bfs.node_id OR e.target = bfs.node_id)
                    WHERE bfs.depth < ?
                      AND bfs.node_id NOT IN ({hub_placeholders})
                      {bfs_rel_clause}
                )
                SELECT DISTINCT node_id FROM bfs
                """
                params: Tuple[Any, ...] = (resolved, depth, *hubs, *relations_filter)
                for row in index.conn.execute(hub_aware_cte, params):
                    visited.add(row["node_id"])
            elif relations_filter:
                filtered_cte = f"""
                WITH RECURSIVE bfs(node_id, depth) AS (
                    SELECT ?, 0
                  UNION
                    SELECT
                        CASE WHEN e.source = bfs.node_id THEN e.target
                             ELSE e.source END,
                        bfs.depth + 1
                    FROM bfs
                    JOIN edges e
                      ON (e.source = bfs.node_id OR e.target = bfs.node_id)
                    WHERE bfs.depth < ?
                      {bfs_rel_clause}
                )
                SELECT DISTINCT node_id FROM bfs
                """
                for row in index.conn.execute(
                    filtered_cte, (resolved, depth, *relations_filter)
                ):
                    visited.add(row["node_id"])
            else:
                for row in index.conn.execute(_BFS_CTE, (resolved, depth)):
                    visited.add(row["node_id"])

            hubs_skipped = len(hubs & visited)

            placeholders = ",".join("?" for _ in visited)
            visited_list = list(visited)

            node_rows: Dict[str, Dict[str, Any]] = {}
            for row in index.conn.execute(
                f"SELECT * FROM nodes WHERE id IN ({placeholders})",
                visited_list,
            ):
                node_rows[row["id"]] = dict(row)

            edge_rel_clause = (
                f"AND relation IN ({','.join('?' for _ in relations_filter)})"
                if relations_filter
                else ""
            )
            selected_edges: Dict[tuple, Dict[str, Any]] = {}
            for row in index.conn.execute(
                f"""
                SELECT * FROM edges
                WHERE source IN ({placeholders})
                  AND target IN ({placeholders})
                  {edge_rel_clause}
                """,
                visited_list + visited_list + list(relations_filter),
            ):
                edge = dict(row)
                key = (edge["source"], edge["target"], edge["relation"], edge.get("metadata") or "")
                selected_edges[key] = edge

            total_nodes = len(visited)
            total_edges = len(selected_edges)
            confidence_breakdown = _confidence_breakdown(selected_edges.values())
            hubs_note = f" Skipped {hubs_skipped} hub(s)." if hubs_skipped else ""
            conf_note = _confidence_note(confidence_breakdown)
            summary = (
                f"{total_nodes} nodes, {total_edges} edges within depth {depth} around "
                f"'{_short_name(resolved, node_rows)}'."
                + hubs_note
                + conf_note
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
    ) -> PathResult:
        if detail_level not in ("minimal", "standard"):
            raise ValueError(f"detail_level must be 'minimal' or 'standard', got '{detail_level}'")
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]

            src = _resolve_graph_node(index, source, repo_root)
            dst = _resolve_graph_node(index, target, repo_root)

            # Build adjacency optionally restricted to the requested relations.
            adj: Dict[str, List[Tuple[str, str, str]]] = {}
            rel_clause, rel_params = _relation_clause(relations)
            for row in index.conn.execute(f"SELECT source, target, relation, confidence_tier FROM edges {rel_clause}", rel_params):
                s, t, r = row["source"], row["target"], row["relation"]
                try:
                    tier = row["confidence_tier"] if row["confidence_tier"] else "EXTRACTED"
                except Exception:
                    tier = "EXTRACTED"
                adj.setdefault(s, []).append((t, r, tier))
                adj.setdefault(t, []).append((s, r, tier))

            # Precompute hub threshold and hub node ids for hub-aware BFS.
            threshold = _compute_hub_threshold(index, relations)
            hubs = _hub_node_ids(index, threshold, relations)
            hubs.discard(src)
            hubs.discard(dst)

            prev: Dict[str, Optional[Tuple[str, str, str]]] = {src: None}
            queue: deque[str] = deque([src])
            found = False
            while queue:
                current = queue.popleft()
                if current == dst:
                    found = True
                    break
                # Hub-aware expansion: do not expand through hub nodes unless they
                # are the source or the destination.
                if current in hubs:
                    continue
                for neighbor, relation, tier in adj.get(current, []):
                    if neighbor not in prev:
                        prev[neighbor] = (current, relation, tier)
                        queue.append(neighbor)

            if not found:
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

            path_ids: List[str] = []
            path_edges: List[PathEdge] = []
            node = dst
            while node is not None:
                path_ids.append(node)
                entry = prev.get(node)
                if entry is not None:
                    parent, relation, tier = entry
                    path_edges.append(PathEdge(source=parent, target=node, relation=relation))
                    node = parent
                else:
                    node = None
            path_ids.reverse()
            path_edges.reverse()

            placeholders = ",".join("?" for _ in path_ids)
            node_rows: Dict[str, Dict[str, Any]] = {}
            for row in index.conn.execute(
                f"SELECT * FROM nodes WHERE id IN ({placeholders})",
                path_ids,
            ):
                node_rows[row["id"]] = dict(row)

            name_chain = " → ".join(_short_name(nid, node_rows) for nid in path_ids)
            summary = f"{name_chain} ({len(path_edges)} hops)"

            # Compute a confidence breakdown over the edges forming the path.
            confidence_counts: Dict[str, int] = {}
            for pe in path_edges:
                row = index.conn.execute(
                    "SELECT confidence_tier FROM edges WHERE source = ? AND target = ? AND relation = ? LIMIT 1",
                    (pe.source, pe.target, pe.relation),
                ).fetchone()
                tier = row["confidence_tier"] if row and row["confidence_tier"] else "EXTRACTED"
                confidence_counts[tier] = confidence_counts.get(tier, 0) + 1

            # Count how many hub nodes were avoided within the path (excluding endpoints).
            hubs_skipped = sum(1 for nid in path_ids if nid in hubs)

            if detail_level == "minimal":
                minimal_steps = [
                    PathStep(
                        node_id=nid,
                        kind="",
                        name=_short_name(nid, node_rows),
                        path="",
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
    row = index.conn.execute(
        "SELECT id FROM nodes WHERE id = ?", (node,)
    ).fetchone()
    if row:
        return row["id"]

    if node == ".":
        repo_row = index.conn.execute(
            "SELECT id FROM nodes WHERE type = 'repo' LIMIT 1"
        ).fetchone()
        if repo_row:
            return repo_row["id"]

    if not repo_root:
        try:
            metadata = index.metadata()
            repo_root = metadata.get("root_dir", "")
        except Exception:
            pass

    repo_basename = Path(repo_root).name if repo_root else ""
    if repo_basename and node == repo_basename:
        repo_row = index.conn.execute(
            "SELECT id FROM nodes WHERE type = 'repo' LIMIT 1"
        ).fetchone()
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
            pass

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
