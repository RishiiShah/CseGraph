"""Integration tests for csegraph community detection."""

from __future__ import annotations

import json
from pathlib import Path

from csegraph_core.graph.communities import detect_communities
from csegraph_core.core.models import to_dict
from csegraph_core.index.services import IndexService


def _index_repo(tmp_path: Path, files: dict[str, str]) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, content in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    db = str(tmp_path / "index.db")
    IndexService(db).index(str(repo), profile="small")
    return db


class TestCommunityDetection:
    def test_detects_communities(self, tmp_path):
        db = _index_repo(tmp_path, {
            "a.py": "from b import helper\n\ndef main():\n    helper()\n",
            "b.py": "def helper():\n    pass\n",
            "c.py": "def standalone():\n    pass\n",
        })
        result = detect_communities(db)
        assert result.command == "communities"
        assert result.num_communities >= 1
        assert len(result.communities) >= 1
        total_nodes = sum(c.size for c in result.communities)
        assert total_nodes >= 3

    def test_empty_repo(self, tmp_path):
        repo = tmp_path / "empty"
        repo.mkdir()
        db = str(tmp_path / "index.db")
        IndexService(db).index(str(repo), profile="small")
        result = detect_communities(db)
        assert result.num_communities == 0
        assert result.communities == []

    def test_writes_community_ids(self, tmp_path):
        db = _index_repo(tmp_path, {
            "x.py": "def foo(): pass\ndef bar(): foo()\n",
        })
        detect_communities(db)

        import sqlite3
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, community_id FROM nodes WHERE type IN ('function','file')"
        ).fetchall()
        conn.close()
        for row in rows:
            assert row["community_id"] is not None

    def test_serializable(self, tmp_path):
        db = _index_repo(tmp_path, {"a.py": "def f(): pass\n"})
        result = detect_communities(db)
        payload = to_dict(result)
        assert isinstance(json.dumps(payload), str)

    def test_modularity_in_range(self, tmp_path):
        db = _index_repo(tmp_path, {
            "a.py": "def a1(): a2()\ndef a2(): pass\n",
            "b.py": "def b1(): b2()\ndef b2(): pass\n",
        })
        result = detect_communities(db)
        assert -0.5 <= result.modularity <= 1.0
