from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Dict, Tuple

from csegraph_core.core.models import GraphEdgeView, GraphNodeView, GraphResult
from csegraph_core.index.loaders import edge_maps, load_edges, load_files, load_nodes, load_symbols
from csegraph_core.index.repository import ProjectIndex, json_loads

_CONTAINS_META = ""


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
            structural = load_nodes(index, project_id, types=("repo", "folder"))
            all_nodes = load_nodes(index, project_id)
            edges = load_edges(index, project_id)
            _add_contains_edges(edges, all_nodes)
            outgoing, incoming = edge_maps(edges)

            resolved = _resolve_graph_node(node_id, symbols, files, structural, repo_root)
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

            nodes = [_node_view(node, symbols, files, structural) for node in sorted(visited)]
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


def _add_contains_edges(
    edges: list,
    all_nodes: Dict[str, Dict[str, Any]],
) -> None:
    existing = {
        (e["source_id"], "contains", e["target_id"])
        for e in edges
        if e["relation"] == "contains"
    }
    for node_id, row in all_nodes.items():
        parent_id = row.get("parent_id")
        if not parent_id or parent_id not in all_nodes or parent_id == node_id:
            continue
        if (parent_id, "contains", node_id) in existing:
            continue
        edges.append({
            "source_id": parent_id,
            "source_node_id": parent_id,
            "target_id": node_id,
            "target_node_id": node_id,
            "relation": "contains",
            "metadata": None,
        })


def _resolve_graph_node(
    node: str,
    symbols: Dict[str, Dict[str, Any]],
    files: Dict[str, Dict[str, Any]],
    structural: Dict[str, Dict[str, Any]],
    repo_root: str = "",
) -> str:
    if node in symbols or node in files or node in structural:
        return node

    resolved_path = str(Path(node).resolve()) if node else ""
    repo_basename = Path(repo_root).name if repo_root else ""
    for node_id, row in structural.items():
        if row.get("type") != "repo":
            continue
        if node == ".":
            return node_id
        if repo_root and resolved_path == str(Path(repo_root).resolve()):
            return node_id
        if repo_basename and node == repo_basename:
            return node_id

    lowered = node.lower()
    for node_id, row in symbols.items():
        if row["name"].lower() == lowered:
            return node_id
    for node_id, row in files.items():
        if row["path"].lower() == lowered:
            return node_id
    for node_id, row in structural.items():
        name = row.get("name", "")
        path = row.get("path", "")
        if name.lower() == lowered or path.lower() == lowered:
            return node_id
    raise ValueError(f"Node '{node}' did not match any indexed file, symbol, or folder.")


def _node_view(
    node_id: str,
    symbols: Dict[str, Dict[str, Any]],
    files: Dict[str, Dict[str, Any]],
    structural: Dict[str, Dict[str, Any]],
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
    if node_id in files:
        row = files[node_id]
        return GraphNodeView(
            node_id=node_id,
            kind="file",
            name=Path(row["path"]).name,
            file_path=row["path"],
        )
    if node_id in structural:
        row = structural[node_id]
        return GraphNodeView(
            node_id=node_id,
            kind=row.get("type", "folder"),
            name=row.get("name", ""),
            file_path=row.get("path", ""),
        )
    return GraphNodeView(
        node_id=node_id,
        kind="external",
        name=node_id.split("::")[-1],
        file_path="",
    )
