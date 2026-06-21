"""Edge-relation filter on csegraph_graph / inspect.

Verifies that passing `relations=[...]` restricts BFS traversal and the returned
edge set to the requested edge kinds.
"""

from __future__ import annotations

from pathlib import Path

from csegraph._core.graph.queries import GraphQueryService
from csegraph._core.index.services import IndexService
from csegraph._core.server.app import _TOOLS, _handle_tool


def _multi_relation_repo(tmp_path: Path) -> tuple[Path, str]:
    """Repo with both `calls` and `imports` edges so we can filter by either."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "from helpers import fmt\n\ndef greet(name: str) -> str:\n    return fmt(name)\n",
        encoding="utf-8",
    )
    (repo / "helpers.py").write_text(
        'def fmt(name: str) -> str:\n    return f"hi {name}"\n',
        encoding="utf-8",
    )
    db = str(repo / ".scratch" / "csegraph" / "test.db")
    IndexService(db).index(repo, profile="small")
    return repo, db


class TestRelationFilterService:
    def test_no_filter_preserves_all_relations(self, tmp_path):
        _, db = _multi_relation_repo(tmp_path)
        result = GraphQueryService(db).neighborhood(
            "file::app.py", depth=2, detail_level="standard"
        )
        relations = {e.relation for e in result.edges}
        assert "calls" in relations
        assert "imports" in relations
        assert result.relations_filter == []

    def test_calls_only(self, tmp_path):
        _, db = _multi_relation_repo(tmp_path)
        result = GraphQueryService(db).neighborhood(
            "file::app.py",
            depth=2,
            detail_level="standard",
            relations=["calls"],
        )
        relations = {e.relation for e in result.edges}
        assert relations <= {"calls"}
        assert result.relations_filter == ["calls"]

    def test_imports_only(self, tmp_path):
        _, db = _multi_relation_repo(tmp_path)
        result = GraphQueryService(db).neighborhood(
            "file::app.py",
            depth=2,
            detail_level="standard",
            relations=["imports"],
        )
        relations = {e.relation for e in result.edges}
        assert relations <= {"imports"}
        assert result.relations_filter == ["imports"]

    def test_empty_list_treated_as_no_filter(self, tmp_path):
        _, db = _multi_relation_repo(tmp_path)
        result = GraphQueryService(db).neighborhood(
            "file::app.py",
            depth=2,
            detail_level="standard",
            relations=[],
        )
        relations = {e.relation for e in result.edges}
        assert len(relations) >= 2
        assert result.relations_filter == []

    def test_filter_shrinks_edge_count(self, tmp_path):
        _, db = _multi_relation_repo(tmp_path)
        full = GraphQueryService(db).neighborhood("file::app.py", depth=2, detail_level="standard")
        calls_only = GraphQueryService(db).neighborhood(
            "file::app.py",
            depth=2,
            detail_level="standard",
            relations=["calls"],
        )
        assert len(calls_only.edges) < len(full.edges)


class TestRelationFilterMcp:
    def test_schema_declares_relations(self):
        graph_tool = next(t for t in _TOOLS if t.name == "csegraph_graph")
        props = graph_tool.inputSchema["properties"]
        assert "relations" in props
        assert props["relations"]["type"] == "array"
        assert props["relations"]["items"] == {"type": "string"}

    def test_handle_tool_applies_filter(self, tmp_path):
        repo, db = _multi_relation_repo(tmp_path)
        result = _handle_tool(
            "csegraph_graph",
            {
                "node": "file::app.py",
                "repo": str(repo),
                "db": db,
                "depth": 2,
                "detail_level": "standard",
                "relations": ["calls"],
            },
        )
        relations = {e["relation"] for e in result["edges"]}
        assert relations <= {"calls"}
        assert result["relations_filter"] == ["calls"]
