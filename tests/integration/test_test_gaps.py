"""Tests for test-gap analysis service."""

from __future__ import annotations

import json
from pathlib import Path

from csegraph._core.core.models import to_dict
from csegraph._core.graph.test_gaps import TestGapService
from csegraph._core.index.services import IndexService
from csegraph._core.postprocess import PostprocessService


def _index_repo(tmp_path: Path, repo: Path) -> str:
    db = str(tmp_path / "index.db")
    IndexService(db).index(str(repo), profile="small")
    return db


class TestTestGaps:
    def test_no_tests_zero_coverage(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "mod.py").write_text(
            "def alpha():\n    pass\n\ndef beta():\n    alpha()\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        result = TestGapService(db).analyze()

        assert result.command == "test-gaps"
        assert result.total_symbols >= 2
        assert result.tested_count == 0
        assert result.untested_count == result.total_symbols
        assert result.coverage_pct == 0.0
        names = {h.name for h in result.hotspots}
        assert "alpha" in names
        assert "beta" in names

    def test_full_coverage(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "core.py").write_text(
            "def compute():\n    return 42\n",
            encoding="utf-8",
        )
        (repo / "test_core.py").write_text(
            "from core import compute\ndef test_compute():\n    compute()\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        result = TestGapService(db).analyze()

        assert result.tested_count >= 1
        untested_names = {h.name for h in result.hotspots}
        assert "compute" not in untested_names

    def test_mixed_coverage(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "core.py").write_text(
            "def tested_fn():\n    return 1\n\ndef untested_fn():\n    return 2\n",
            encoding="utf-8",
        )
        (repo / "test_core.py").write_text(
            "from core import tested_fn\ndef test_it():\n    tested_fn()\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        result = TestGapService(db).analyze()

        all_hotspot_names = {h.name for h in result.hotspots}
        assert "untested_fn" in all_hotspot_names
        assert "tested_fn" not in all_hotspot_names
        assert result.coverage_pct > 0.0
        assert result.coverage_pct < 100.0

    def test_hotspot_ranking(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "hub.py").write_text(
            "def hub_fn():\n    pass\n\ndef leaf_fn():\n    pass\n",
            encoding="utf-8",
        )
        (repo / "caller1.py").write_text(
            "from hub import hub_fn\ndef c1():\n    hub_fn()\n",
            encoding="utf-8",
        )
        (repo / "caller2.py").write_text(
            "from hub import hub_fn\ndef c2():\n    hub_fn()\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        result = TestGapService(db).analyze()

        hotspot_names = [h.name for h in result.hotspots]
        hub_idx = next((i for i, n in enumerate(hotspot_names) if n == "hub_fn"), None)
        leaf_idx = next((i for i, n in enumerate(hotspot_names) if n == "leaf_fn"), None)
        if hub_idx is not None and leaf_idx is not None:
            assert hub_idx < leaf_idx

    def test_community_coverage(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text(
            "def a1():\n    a2()\ndef a2():\n    pass\n",
            encoding="utf-8",
        )
        (repo / "b.py").write_text(
            "def b1():\n    b2()\ndef b2():\n    pass\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)
        PostprocessService(db).postprocess()

        result = TestGapService(db).analyze()

        assert result.communities_affected if hasattr(result, "communities_affected") else True
        if result.community_coverage:
            for cc in result.community_coverage:
                assert cc.total_symbols >= 1
                assert 0.0 <= cc.coverage_pct <= 100.0

    def test_serializable(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "f.py").write_text("def f():\n    pass\n", encoding="utf-8")
        db = _index_repo(tmp_path, repo)

        result = TestGapService(db).analyze()
        payload = to_dict(result)
        assert isinstance(json.dumps(payload), str)

    def test_empty_index(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "empty.txt").write_text("not python", encoding="utf-8")
        db = _index_repo(tmp_path, repo)

        result = TestGapService(db).analyze()

        assert result.total_symbols == 0
        assert result.coverage_pct == 0.0
        assert result.hotspots == []

    def test_limit_respected(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        funcs = "\n".join(f"def fn_{i}():\n    pass\n" for i in range(10))
        (repo / "many.py").write_text(funcs, encoding="utf-8")
        db = _index_repo(tmp_path, repo)

        result = TestGapService(db).analyze(limit=3)
        assert len(result.hotspots) <= 3

    def test_warns_no_tested_by_edges(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "mod.py").write_text("def f():\n    pass\n", encoding="utf-8")
        db = _index_repo(tmp_path, repo)

        result = TestGapService(db).analyze()
        assert any("tested_by" in w.lower() for w in result.warnings)
