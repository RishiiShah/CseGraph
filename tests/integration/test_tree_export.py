"""Integration tests for HTML file tree visualization."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from csegraph._core.graph.tree import TreeExportService
from csegraph._core.index.services import IndexService


def _index_repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "class App:\n    def run(self):\n        pass\n",
        encoding="utf-8",
    )
    sub = repo / "utils"
    sub.mkdir()
    (sub / "__init__.py").write_text("", encoding="utf-8")
    (sub / "helpers.py").write_text("def helper(): pass\n", encoding="utf-8")
    db = str(repo / ".scratch" / "csegraph" / "index.db")
    IndexService(db).index(str(repo), profile="small")
    return db


class TestTreeExport:
    def test_exports_html_file(self, tmp_path):
        db = _index_repo(tmp_path)
        output = str(tmp_path / "repo" / ".scratch" / "csegraph" / "tree.html")
        result = TreeExportService(db).export(output)
        assert Path(output).exists()
        content = Path(output).read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "csegraph file tree" in content

    def test_result_fields(self, tmp_path):
        db = _index_repo(tmp_path)
        output = str(tmp_path / "repo" / ".scratch" / "csegraph" / "tree.html")
        result = TreeExportService(db).export(output)
        assert result.command == "tree"
        assert result.total_nodes > 0
        assert result.output_path == output

    def test_contains_node_data(self, tmp_path):
        db = _index_repo(tmp_path)
        output = str(tmp_path / "repo" / ".scratch" / "csegraph" / "tree.html")
        TreeExportService(db).export(output)
        content = Path(output).read_text(encoding="utf-8")
        assert "App" in content
        assert "helper" in content

    def test_search_input_present(self, tmp_path):
        db = _index_repo(tmp_path)
        output = str(tmp_path / "repo" / ".scratch" / "csegraph" / "tree.html")
        TreeExportService(db).export(output)
        content = Path(output).read_text(encoding="utf-8")
        assert 'id="search"' in content

    def test_allows_repo_local_scratch_output(self, tmp_path):
        db = _index_repo(tmp_path)
        output = tmp_path / "repo" / ".scratch" / "csegraph" / "tree.html"

        result = TreeExportService(db).export(output)

        assert result.output_path == str(output.resolve())
        assert output.exists()

    def test_rejects_system_tempdir_output(self, tmp_path):
        db = _index_repo(tmp_path)
        output = Path(tempfile.gettempdir()) / f"{tmp_path.name}-tree.html"

        with pytest.raises(ValueError):
            TreeExportService(db).export(output)
