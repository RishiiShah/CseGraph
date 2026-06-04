"""Integration tests for P5 — Performance.

Covers:
  P5-1  timing instrumentation in services
  P5-2  shortest_path via SQLite CTE (no Python adjacency list)
  P5-3  postprocess levels (none / minimal / full)
  P5-4  bounded dependent expansion in refresh
  P5-5  benchmark fixtures for index, refresh, search, context-token size
"""

from __future__ import annotations

from pathlib import Path

import pytest

from csegraph._core.index.services import IndexService, RefreshService
from csegraph._core.postprocess import POSTPROCESS_LEVELS, PostprocessService
from csegraph._core.retrieval.context import ContextService
from csegraph._core.graph.queries import GraphQueryService


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "from helpers import fmt\n\n"
        "class Greeter:\n"
        "    def greet(self, name: str) -> str:\n"
        '        """Say hello."""\n'
        "        return fmt(name)\n",
        encoding="utf-8",
    )
    (repo / "helpers.py").write_text(
        "def fmt(name: str) -> str:\n"
        '    return f"Hello, {name}"\n\n'
        "def upper(value: str) -> str:\n"
        "    return value.upper()\n",
        encoding="utf-8",
    )
    (repo / "test_app.py").write_text(
        "from app import Greeter\n\n"
        "def test_greet():\n"
        '    assert Greeter().greet("world") == "Hello, world"\n',
        encoding="utf-8",
    )
    return repo


def _index(tmp_path: Path, repo: Path, profile: str = "small") -> str:
    db = str(tmp_path / "index.db")
    IndexService(db).index(repo, profile=profile)
    PostprocessService(db).postprocess(level="full")
    return db


# ---------------------------------------------------------------------------
# P5-1: Timing instrumentation
# ---------------------------------------------------------------------------


class TestTimingInstrumentation:
    def test_refresh_result_has_timings(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = RefreshService(db).refresh(profile="small")
        assert isinstance(result.timings_ms, dict)
        assert "detect_changes" in result.timings_ms
        assert "parse_changed" in result.timings_ms

    def test_refresh_with_changes_has_write_timing(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        (repo / "helpers.py").write_text(
            "def fmt(name: str) -> str:\n"
            '    return f"Hi, {name}"\n',
            encoding="utf-8",
        )
        result = RefreshService(db).refresh(profile="small")
        assert result.files_indexed > 0
        assert "write_graph" in result.timings_ms
        assert "delete_old" in result.timings_ms

    def test_context_result_has_timings(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = ContextService(db).build_context(
            task="greet a user", profile="small",
        )
        assert isinstance(result.timings_ms, dict)
        assert "load_data" in result.timings_ms
        assert "scoring" in result.timings_ms
        assert "graph_expansion" in result.timings_ms
        assert "detail_pass" in result.timings_ms

    def test_postprocess_result_has_timings(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = PostprocessService(db).postprocess(level="full")
        assert isinstance(result.timings_ms, dict)
        assert "fts_rebuild_ms" in result.timings_ms
        assert "community_detection_ms" in result.timings_ms


# ---------------------------------------------------------------------------
# P5-2: Shortest path via SQLite CTE
# ---------------------------------------------------------------------------


class TestShortestPathCTE:
    def test_direct_path_found(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = GraphQueryService(db).shortest_path("Greeter.greet", "fmt")
        assert result.found is True
        assert result.length >= 1
        assert len(result.nodes) >= 2

    def test_same_node_path(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = GraphQueryService(db).shortest_path("fmt", "fmt")
        assert result.found is True
        assert result.length == 0

    def test_path_with_relations_filter(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = GraphQueryService(db).shortest_path(
            "Greeter.greet", "fmt", relations=["calls"],
        )
        assert result.found is True
        assert result.relations_filter == ["calls"]

    def test_path_standard_detail(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = GraphQueryService(db).shortest_path(
            "Greeter.greet", "fmt", detail_level="standard",
        )
        assert result.found is True
        assert result.detail_level == "standard"
        assert len(result.edges) >= 1

    def test_path_confidence_breakdown(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = GraphQueryService(db).shortest_path("Greeter.greet", "fmt")
        assert isinstance(result.confidence_breakdown, dict)


# ---------------------------------------------------------------------------
# P5-3: Postprocess levels
# ---------------------------------------------------------------------------


class TestPostprocessLevels:
    def test_level_constants(self):
        assert "none" in POSTPROCESS_LEVELS
        assert "minimal" in POSTPROCESS_LEVELS
        assert "full" in POSTPROCESS_LEVELS

    def test_level_none_skips_all(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = PostprocessService(db).postprocess(level="none")
        assert result.level == "none"
        assert "fts" in result.skipped
        assert "communities" in result.skipped
        assert result.fts_entries == 0
        assert result.communities_detected == 0

    def test_level_minimal_runs_fts_only(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = PostprocessService(db).postprocess(level="minimal")
        assert result.level == "minimal"
        assert result.fts_entries > 0
        assert "communities" in result.skipped
        assert result.communities_detected == 0

    def test_level_full_runs_all(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = PostprocessService(db).postprocess(level="full")
        assert result.level == "full"
        assert result.fts_entries > 0
        assert "communities" not in result.skipped

    def test_invalid_level_raises(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        with pytest.raises(ValueError, match="level must be one of"):
            PostprocessService(db).postprocess(level="turbo")

    def test_level_minimal_has_timing(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = PostprocessService(db).postprocess(level="minimal")
        assert "fts_rebuild_ms" in result.timings_ms
        assert "community_detection_ms" not in result.timings_ms

    def test_no_fts_flag_still_works_with_level_full(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = PostprocessService(db).postprocess(level="full", no_fts=True)
        assert "fts" in result.skipped
        assert result.fts_entries == 0


# ---------------------------------------------------------------------------
# P5-4: Bounded dependent expansion in refresh
# ---------------------------------------------------------------------------


class TestDependentExpansion:
    def test_refresh_reports_dependents_expanded(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        (repo / "helpers.py").write_text(
            "def fmt(name: str) -> str:\n"
            '    return f"Hi, {name}!"\n\n'
            "def upper(value: str) -> str:\n"
            "    return value.upper()\n",
            encoding="utf-8",
        )
        result = RefreshService(db).refresh(profile="small")
        assert result.files_indexed >= 1
        assert isinstance(result.dependents_expanded, int)
        assert isinstance(result.dependents_cap_hit, bool)
        assert "dependent_expansion" in result.timings_ms

    def test_refresh_zero_limit_skips_expansion(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        (repo / "helpers.py").write_text(
            "def fmt(name: str) -> str:\n"
            '    return f"Hi, {name}!"\n',
            encoding="utf-8",
        )
        result = RefreshService(db).refresh(profile="small", dependents_limit=0)
        assert result.dependents_expanded == 0

    def test_refresh_cap_hit_when_many_dependents(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        (repo / "helpers.py").write_text(
            "def fmt(name: str) -> str:\n"
            '    return f"Yo, {name}"\n',
            encoding="utf-8",
        )
        result = RefreshService(db).refresh(profile="small", dependents_limit=1)
        assert isinstance(result.dependents_cap_hit, bool)


# ---------------------------------------------------------------------------
# P5-5: Benchmark fixtures
# ---------------------------------------------------------------------------


class TestBenchmarkFixtures:
    def test_index_timing(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "bench.db")
        result = IndexService(db).index(repo, profile="small")
        assert result.files_indexed == 3
        assert result.symbols_indexed > 0
        assert result.edges_indexed > 0
        assert "discover_parse" in result.timings_ms
        assert "write_graph" in result.timings_ms
        assert all(v >= 0 for v in result.timings_ms.values())

    def test_refresh_no_change_is_fast(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = RefreshService(db).refresh(profile="small")
        assert result.files_indexed == 0
        assert result.timings_ms.get("detect_changes", 0) >= 0

    def test_context_token_size_bounded(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = ContextService(db).build_context(
            task="greet a user",
            profile="small",
            include_source="never",
        )
        assert result.total_estimated_tokens > 0
        assert result.total_estimated_tokens < 50000

    def test_context_with_source_larger_than_without(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        no_src = ContextService(db).build_context(
            task="greet a user", profile="small", include_source="never",
        )
        with_src = ContextService(db).build_context(
            task="greet a user", profile="small", include_source="always",
        )
        assert with_src.total_estimated_tokens >= no_src.total_estimated_tokens

    def test_postprocess_fts_count(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = PostprocessService(db).postprocess(level="full")
        assert result.fts_entries >= 3

    def test_neighborhood_returns_nodes(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = GraphQueryService(db).neighborhood("fmt", depth=1)
        assert result.total_nodes >= 1

    def test_index_then_refresh_cycle(self, tmp_path):
        """Full index → modify → refresh cycle completes with sane numbers."""
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "cycle.db")
        idx = IndexService(db).index(repo, profile="small")
        PostprocessService(db).postprocess(level="full")
        assert idx.files_indexed == 3

        (repo / "app.py").write_text(
            "from helpers import fmt, upper\n\n"
            "class Greeter:\n"
            "    def greet(self, name: str) -> str:\n"
            "        return fmt(upper(name))\n",
            encoding="utf-8",
        )
        ref = RefreshService(db).refresh(profile="small")
        assert ref.files_indexed >= 1
        assert ref.edges_indexed >= idx.edges_indexed - 5

    def test_search_returns_results_for_known_symbol(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = _index(tmp_path, repo)
        result = ContextService(db).build_context(
            task="fmt helper function", profile="small",
        )
        node_names = [n.name for n in result.nodes]
        assert any("fmt" in name for name in node_names)
