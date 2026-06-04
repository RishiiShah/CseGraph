"""Self-contained HTML file tree visualization from the SQLite index."""
from __future__ import annotations

import html
import json
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List

from csegraph._core.core.models import VisualExportResult
from csegraph._core.core.paths import assert_repo_local_path
from csegraph._core.index.loaders import load_nodes
from csegraph._core.index.repository import ProjectIndex


class TreeExportService:
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
            tree_nodes = _build_tree_nodes(all_nodes)

            content = _render_tree_html(repo_root, tree_nodes)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8")

            return VisualExportResult(
                command="tree",
                db_path=self.db_path,
                repo_root=repo_root,
                output_path=str(output),
                total_nodes=len(tree_nodes),
                total_edges=0,
            )
        finally:
            index.close()


def _build_tree_nodes(all_nodes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for node_id, row in sorted(all_nodes.items()):
        start = row.get("start_line")
        end = row.get("end_line")
        result.append({
            "id": node_id,
            "name": row.get("name", ""),
            "kind": row.get("type") or row.get("kind", ""),
            "path": row.get("path") or "",
            "parent_id": row.get("parent_id"),
            "language": row.get("language") or "",
            "signature": row.get("signature") or "",
            "line_range": [int(start), int(end)] if start is not None and end is not None else None,
        })
    return result


def _render_tree_html(repo_root: str, nodes: List[Dict[str, Any]]) -> str:
    return _load_template("tree.html").replace(
        "__CSEGRAPH_REPO_NAME__",
        html.escape(Path(repo_root).name),
    ).replace("__CSEGRAPH_DATA_JSON__", json.dumps(nodes, separators=(",", ":")))


def _load_template(name: str) -> str:
    return resources.files("csegraph._core.graph.templates").joinpath(name).read_text(encoding="utf-8")
