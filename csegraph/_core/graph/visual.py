"""Self-contained HTML graph export from the SQLite index."""
from __future__ import annotations

import html
import json
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List

from csegraph._core.core.models import VisualExportResult
from csegraph._core.core.paths import assert_repo_local_path
from csegraph._core.index.loaders import load_edges, load_nodes
from csegraph._core.index.repository import ProjectIndex, json_loads


class VisualExportService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def export(self, output_path: str | Path) -> VisualExportResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]
            repo_root_path = Path(repo_root).resolve()
            output = assert_repo_local_path(output_path, repo_root_path, "Output")

            all_nodes = load_nodes(index)
            edges = load_edges(index)

            graph_nodes = _build_graph_nodes(all_nodes)
            graph_edges = _build_graph_edges(all_nodes, edges)

            content = _render_html(repo_root, graph_nodes, graph_edges)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8")

            return VisualExportResult(
                command="graph",
                db_path=self.db_path,
                repo_root=repo_root,
                output_path=str(output),
                total_nodes=len(graph_nodes),
                total_edges=len(graph_edges),
            )
        finally:
            index.close()


def _build_graph_nodes(all_nodes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    child_counts: Dict[str, int] = {}
    for row in all_nodes.values():
        parent_id = row.get("parent_id")
        if parent_id and parent_id in all_nodes:
            child_counts[parent_id] = child_counts.get(parent_id, 0) + 1

    for node_id, row in sorted(all_nodes.items()):
        result.append({
            "id": node_id,
            "name": row.get("name", ""),
            "kind": row.get("type") or row.get("kind", ""),
            "path": row.get("path") or row.get("file_path", ""),
            "parent_id": row.get("parent_id"),
            "child_count": child_counts.get(node_id, 0),
            "line_range": _line_range(row.get("start_line"), row.get("end_line")),
            "search_text": _search_text(
                node_id,
                row.get("name", ""),
                row.get("path") or row.get("file_path", ""),
            ),
        })
    return result


def _search_text(node_id: str, name: Any, path: Any) -> str:
    return " ".join(str(part).lower() for part in (name, path, node_id) if part)


def _line_range(start_line: Any, end_line: Any) -> List[int] | None:
    if start_line is None or end_line is None:
        return None
    return [int(start_line), int(end_line)]


def _build_graph_edges(
    all_nodes: Dict[str, Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    deduped: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for edge in edges:
        key = (edge["source_id"], edge["relation"], edge["target_id"])
        deduped[key] = {
            "source": edge["source_id"],
            "target": edge["target_id"],
            "relation": edge["relation"],
            "metadata": json_loads(edge.get("metadata")),
            "confidence": float(edge.get("confidence", 1.0)),
            "confidence_tier": edge.get("confidence_tier") or "EXTRACTED",
        }

    result: List[Dict[str, Any]] = []
    for _key, edge in sorted(deduped.items()):
        result.append(edge)
    return result


def _render_html(
    repo_root: str,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> str:
    data_json = json.dumps(
        {
            "schema_version": "csegraph-graph-v1",
            "root_dir": repo_root,
            "summary": {"node_count": len(nodes), "edge_count": len(edges)},
            "nodes": nodes,
            "edges": edges,
        },
        separators=(",", ":"),
    )
    return _load_template("graph.html").replace(
        "__CSEGRAPH_REPO_NAME__",
        html.escape(Path(repo_root).name),
    ).replace("__CSEGRAPH_DATA_JSON__", data_json)


def _load_template(name: str) -> str:
    return resources.files("csegraph._core.graph.templates").joinpath(name).read_text(encoding="utf-8")
