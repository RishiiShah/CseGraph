"""Self-contained HTML file tree visualization from the SQLite index."""
from __future__ import annotations

import html
import json
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List

from csegraph_core.core.models import VisualExportResult
from csegraph_core.index.loaders import load_nodes
from csegraph_core.index.repository import ProjectIndex


class TreeExportService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def export(self, output_path: str | Path) -> VisualExportResult:
        output = Path(output_path).resolve()
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]

            repo_root_path = Path(repo_root).resolve()
            import tempfile
            def assert_safe_path(path: Path, repo_path: Path, name: str) -> None:
                resolved_path = path.resolve()
                if resolved_path.is_relative_to(repo_path.resolve()):
                    return
                temp_dir = Path(tempfile.gettempdir()).resolve()
                if resolved_path.is_relative_to(temp_dir):
                    return
                try:
                    home_dir = Path.home().resolve()
                    if resolved_path.is_relative_to(home_dir):
                        return
                except Exception:
                    pass
                try:
                    cwd_dir = Path.cwd().resolve()
                    if resolved_path.is_relative_to(cwd_dir):
                        return
                except Exception:
                    pass
                raise ValueError(f"{name} path '{path}' must be within repository root, home directory, temporary directory, or CWD.")

            assert_safe_path(output, repo_root_path, "Output")

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
    return resources.files("csegraph_core.graph.templates").joinpath(name).read_text(encoding="utf-8")
