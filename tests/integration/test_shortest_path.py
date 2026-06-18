"""Integration tests for csegraph path (shortest path between two nodes)."""

from __future__ import annotations

from pathlib import Path

from csegraph._core.graph.queries import GraphQueryService
from csegraph._core.index.services import IndexService


def _index_repo(tmp_path: Path, files: dict[str, str]) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, content in files.items():
        (repo / name).write_text(content, encoding="utf-8")
    db = str(tmp_path / "index.db")
    IndexService(db).index(str(repo), profile="small")
    return db


class TestShortestPath:
    def test_direct_call_edge(self, tmp_path):
        db = _index_repo(
            tmp_path,
            {
                "main.py": "from lib import helper\n\ndef main():\n    helper()\n",
                "lib.py": "def helper():\n    pass\n",
            },
        )
        result = GraphQueryService(db).shortest_path("main", "helper")
        assert result.found is True
        assert result.length >= 1
        node_ids = [n.node_id for n in result.nodes]
        assert len(node_ids) >= 2

    def test_same_node(self, tmp_path):
        db = _index_repo(
            tmp_path,
            {
                "a.py": "def foo(): pass\n",
            },
        )
        result = GraphQueryService(db).shortest_path("foo", "foo")
        assert result.found is True
        assert result.length == 0
        assert len(result.nodes) == 1

    def test_connected_via_contains(self, tmp_path):
        db = _index_repo(
            tmp_path,
            {
                "a.py": "def alpha(): pass\n",
                "b.py": "def beta(): pass\n",
            },
        )
        result = GraphQueryService(db).shortest_path("alpha", "beta")
        assert result.found is True
        assert result.length >= 1
        ids = [n.node_id for n in result.nodes]
        assert ids[0].endswith("alpha")
        assert ids[-1].endswith("beta")

    def test_multi_hop(self, tmp_path):
        db = _index_repo(
            tmp_path,
            {
                "a.py": "from b import mid\n\ndef start():\n    mid()\n",
                "b.py": "from c import end\n\ndef mid():\n    end()\n",
                "c.py": "def end():\n    pass\n",
            },
        )
        result = GraphQueryService(db).shortest_path("start", "end")
        assert result.found is True
        assert result.length >= 1

    def test_result_serializes(self, tmp_path):
        import json

        from csegraph._core.core.models import to_dict

        db = _index_repo(
            tmp_path,
            {
                "x.py": "def one(): two()\ndef two(): pass\n",
            },
        )
        result = GraphQueryService(db).shortest_path("one", "two")
        payload = to_dict(result)
        assert isinstance(json.dumps(payload), str)
        assert payload["command"] == "path"

    def test_command_field(self, tmp_path):
        db = _index_repo(
            tmp_path,
            {
                "a.py": "def foo(): pass\n",
            },
        )
        result = GraphQueryService(db).shortest_path("foo", "foo")
        assert result.command == "path"
