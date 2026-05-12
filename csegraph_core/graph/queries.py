from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Set

from csegraph_core.core.models import GraphEdgeView, GraphNodeView, GraphResult
from csegraph_core.index.repository import ProjectIndex, json_loads


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


class GraphQueryService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def neighborhood(self, node_id: str, depth: int = 1) -> GraphResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]

            resolved = _resolve_graph_node(index, node_id, repo_root)

            visited: Set[str] = set()
            for row in index.conn.execute(_BFS_CTE, (resolved, depth)):
                visited.add(row["node_id"])

            placeholders = ",".join("?" for _ in visited)
            visited_list = list(visited)

            node_rows: Dict[str, Dict[str, Any]] = {}
            for row in index.conn.execute(
                f"SELECT * FROM nodes WHERE id IN ({placeholders})",
                visited_list,
            ):
                node_rows[row["id"]] = dict(row)

            selected_edges: Dict[tuple, Dict[str, Any]] = {}
            for row in index.conn.execute(
                f"""
                SELECT * FROM edges
                WHERE source IN ({placeholders})
                  AND target IN ({placeholders})
                """,
                visited_list + visited_list,
            ):
                edge = dict(row)
                key = (edge["source"], edge["target"], edge["relation"], edge.get("metadata") or "")
                selected_edges[key] = edge

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
            )
        finally:
            index.close()


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

    repo_basename = Path(repo_root).name if repo_root else ""
    if repo_basename and node == repo_basename:
        repo_row = index.conn.execute(
            "SELECT id FROM nodes WHERE type = 'repo' LIMIT 1"
        ).fetchone()
        if repo_row:
            return repo_row["id"]

    if repo_root:
        resolved_path = str(Path(node).resolve())
        if resolved_path == str(Path(repo_root).resolve()):
            repo_row = index.conn.execute(
                "SELECT id FROM nodes WHERE type = 'repo' LIMIT 1"
            ).fetchone()
            if repo_row:
                return repo_row["id"]

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
            name=node_id.split("::")[-1],
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
