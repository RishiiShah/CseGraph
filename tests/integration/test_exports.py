"""Integration tests for GraphML, Obsidian, and JSON graph exports."""

from __future__ import annotations

import json
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from csegraph_core.core.models import to_dict
from csegraph_core.graph.exports import EXPORT_FORMATS, ExportService
from csegraph_core.index.services import IndexService
from csegraph_core.postprocess import PostprocessService


def _index_repo(tmp_path: Path, files: dict[str, str]) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, content in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    db = str(repo / ".scratch" / "csegraph" / "index.db")
    IndexService(db).index(str(repo), profile="small")
    PostprocessService(db).postprocess(level="full")
    return db


_SAMPLE_FILES = {
    "app.py": "from helpers import fmt\n\ndef greet(name):\n    return fmt(name)\n",
    "helpers.py": "def fmt(name):\n    return f'Hello, {name}'\n",
    "tests/test_app.py": "from app import greet\n\ndef test_greet():\n    assert greet('x')\n",
}


class TestGraphMLExport:
    def test_writes_graphml_file(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        out = str(tmp_path / "repo" / ".scratch" / "csegraph" / "graph.graphml")
        result = ExportService(db).export(out, fmt="graphml")
        assert result.command == "export"
        assert result.format == "graphml"
        assert result.files_written == 1
        assert Path(out).exists()

    def test_graphml_is_valid_xml(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        out = str(tmp_path / "repo" / ".scratch" / "csegraph" / "graph.graphml")
        ExportService(db).export(out, fmt="graphml")
        tree = ET.parse(out)
        root = tree.getroot()
        ns = "http://graphml.graphstruct.org/xmlns"
        graph = root.find(f"{{{ns}}}graph")
        assert graph is not None

    def test_graphml_contains_nodes_and_edges(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        out = str(tmp_path / "repo" / ".scratch" / "csegraph" / "graph.graphml")
        result = ExportService(db).export(out, fmt="graphml")
        tree = ET.parse(out)
        root = tree.getroot()
        ns = "http://graphml.graphstruct.org/xmlns"
        graph = root.find(f"{{{ns}}}graph")
        nodes = graph.findall(f"{{{ns}}}node")
        edges = graph.findall(f"{{{ns}}}edge")
        assert len(nodes) >= 3
        assert len(edges) >= 1
        assert result.total_nodes >= 3
        assert result.total_edges >= 1

    def test_graphml_node_attributes(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        out = str(tmp_path / "repo" / ".scratch" / "csegraph" / "graph.graphml")
        ExportService(db).export(out, fmt="graphml")
        content = Path(out).read_text(encoding="utf-8")
        assert "d_name" in content
        assert "d_type" in content
        assert "d_path" in content

    def test_graphml_allows_repo_local_scratch_output(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        output = tmp_path / "repo" / ".scratch" / "csegraph" / "graph.graphml"

        result = ExportService(db).export(str(output), fmt="graphml")

        assert result.output_path == str(output.resolve())
        assert output.exists()

    def test_graphml_rejects_tmp_output(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        temp_output = Path(tempfile.gettempdir()) / f"{tmp_path.name}-graph.graphml"

        with pytest.raises(ValueError):
            ExportService(db).export(str(temp_output), fmt="graphml")


class TestObsidianExport:
    def test_creates_vault_directory(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        out = str(tmp_path / "repo" / ".scratch" / "csegraph" / "vault")
        result = ExportService(db).export(out, fmt="obsidian")
        assert result.format == "obsidian"
        assert result.files_written >= 3
        assert Path(out).is_dir()

    def test_creates_markdown_notes(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        out = str(tmp_path / "repo" / ".scratch" / "csegraph" / "vault")
        ExportService(db).export(out, fmt="obsidian")
        md_files = list(Path(out).glob("*.md"))
        assert len(md_files) >= 3

    def test_notes_contain_wikilinks(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        out = str(tmp_path / "repo" / ".scratch" / "csegraph" / "vault")
        ExportService(db).export(out, fmt="obsidian")
        found_link = False
        for md in Path(out).glob("*.md"):
            text = md.read_text(encoding="utf-8")
            if "[[" in text and "]]" in text:
                found_link = True
                break
        assert found_link

    def test_communities_index_created(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        out = str(tmp_path / "repo" / ".scratch" / "csegraph" / "vault")
        ExportService(db).export(out, fmt="obsidian")
        comm_index = Path(out) / "_communities.md"
        assert comm_index.exists()
        content = comm_index.read_text(encoding="utf-8")
        assert "Community" in content

    def test_note_has_metadata(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        out = str(tmp_path / "repo" / ".scratch" / "csegraph" / "vault")
        ExportService(db).export(out, fmt="obsidian")
        md_files = list(Path(out).glob("*.md"))
        non_index = [f for f in md_files if f.name != "_communities.md"]
        assert non_index
        content = non_index[0].read_text(encoding="utf-8")
        assert "**Type**" in content
        assert "**Path**" in content


class TestJSONExport:
    def test_writes_json_file(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        out = str(tmp_path / "repo" / ".scratch" / "csegraph" / "export.json")
        result = ExportService(db).export(out, fmt="json")
        assert result.format == "json"
        assert result.files_written == 1
        assert Path(out).exists()

    def test_json_is_valid(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        out = str(tmp_path / "repo" / ".scratch" / "csegraph" / "export.json")
        ExportService(db).export(out, fmt="json")
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert data["schema_version"] == "csegraph-export-v1"
        assert len(data["nodes"]) >= 3
        assert len(data["edges"]) >= 1

    def test_json_nodes_have_fields(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        out = str(tmp_path / "repo" / ".scratch" / "csegraph" / "export.json")
        ExportService(db).export(out, fmt="json")
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        for node in data["nodes"]:
            assert "id" in node
            assert "name" in node
            assert "type" in node
            assert "path" in node

    def test_json_edges_have_fields(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        out = str(tmp_path / "repo" / ".scratch" / "csegraph" / "export.json")
        ExportService(db).export(out, fmt="json")
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        for edge in data["edges"]:
            assert "source" in edge
            assert "target" in edge
            assert "relation" in edge
            assert "confidence" in edge


class TestExportGeneral:
    def test_invalid_format_raises(self, tmp_path):
        db = _index_repo(tmp_path, {"a.py": "x = 1\n"})
        with pytest.raises(ValueError, match="Unknown export format"):
            ExportService(db).export(
                str(tmp_path / "repo" / ".scratch" / "csegraph" / "out"),
                fmt="csv",
            )

    def test_export_formats_constant(self):
        assert "graphml" in EXPORT_FORMATS
        assert "obsidian" in EXPORT_FORMATS
        assert "json" in EXPORT_FORMATS

    def test_result_serializable(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        out = str(tmp_path / "repo" / ".scratch" / "csegraph" / "graph.graphml")
        result = ExportService(db).export(out, fmt="graphml")
        payload = to_dict(result)
        assert isinstance(json.dumps(payload), str)

    def test_empty_repo(self, tmp_path):
        repo = tmp_path / "empty"
        repo.mkdir()
        db = str(repo / ".scratch" / "csegraph" / "index.db")
        IndexService(db).index(str(repo), profile="small")
        out = str(repo / ".scratch" / "csegraph" / "graph.graphml")
        result = ExportService(db).export(out, fmt="graphml")
        assert result.total_nodes <= 1
        assert result.total_edges == 0


class TestExportMCP:
    def test_tool_is_cli_only(self):
        from csegraph_core.server.app import _handle_tool

        with pytest.raises(ValueError, match="Unknown tool"):
            _handle_tool("csegraph_export", {})

    def test_prompt_is_not_agent_facing(self):
        from csegraph_core.server.app import _handle_prompt

        with pytest.raises(ValueError, match="Unknown prompt"):
            _handle_prompt("csegraph-export", {"repo": "/repo"})
