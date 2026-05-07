from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Dict, Tuple

from csegraph.core.models import GraphEdgeView, GraphNodeView, GraphResult
from csegraph.index.loaders import edge_maps, load_edges, load_files, load_symbols
from csegraph.index.repository import ProjectIndex, json_loads


class GraphQueryService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def neighborhood(self, node_id: str, depth: int = 1) -> GraphResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            project = index.get_project()
            project_id = int(project["id"])
            repo_root = project["root_dir"]
            symbols = load_symbols(index, project_id)
            files = load_files(index, project_id)
            edges = load_edges(index, project_id)
            outgoing, incoming = edge_maps(edges)

            resolved = _resolve_graph_node(node_id, symbols, files)
            visited = {resolved}
            queue: deque[Tuple[str, int]] = deque([(resolved, 0)])
            selected_edges: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}

            while queue:
                current, current_depth = queue.popleft()
                if current_depth >= depth:
                    continue
                for edge in outgoing.get(current, []) + incoming.get(current, []):
                    key = (
                        edge["source_id"],
                        edge["target_id"],
                        edge["relation"],
                        edge.get("metadata") or "",
                    )
                    selected_edges[key] = edge
                    neighbor = edge["target_id"] if edge["source_id"] == current else edge["source_id"]
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, current_depth + 1))

            nodes = [_node_view(node, symbols, files) for node in sorted(visited)]
            graph_edges = [
                GraphEdgeView(
                    source=edge["source_id"],
                    target=edge["target_id"],
                    relation=edge["relation"],
                    metadata=json_loads(edge.get("metadata")),
                )
                for edge in sorted(
                    selected_edges.values(),
                    key=lambda item: (item["source_id"], item["relation"], item["target_id"]),
                )
            ]
            return GraphResult(
                command="graph",
                db_path=self.db_path,
                repo_root=repo_root,
                node_id=resolved,
                depth=depth,
                nodes=nodes,
                edges=graph_edges,
            )
        finally:
            index.close()


def _resolve_graph_node(
    node: str,
    symbols: Dict[str, Dict[str, Any]],
    files: Dict[str, Dict[str, Any]],
) -> str:
    if node in symbols or node in files:
        return node
    lowered = node.lower()
    for node_id, row in symbols.items():
        if row["name"].lower() == lowered:
            return node_id
    for node_id, row in files.items():
        if row["path"].lower() == lowered:
            return node_id
    raise ValueError(f"Node '{node}' did not match any indexed file or symbol.")


def _node_view(
    node_id: str,
    symbols: Dict[str, Dict[str, Any]],
    files: Dict[str, Dict[str, Any]],
) -> GraphNodeView:
    if node_id in symbols:
        row = symbols[node_id]
        return GraphNodeView(
            node_id=node_id,
            kind=row["kind"],
            name=row["name"],
            file_path=row["file_path"],
            start_line=row["start_line"],
            end_line=row["end_line"],
        )
    row = files[node_id]
    return GraphNodeView(
        node_id=node_id,
        kind="file",
        name=Path(row["path"]).name,
        file_path=row["path"],
    )
