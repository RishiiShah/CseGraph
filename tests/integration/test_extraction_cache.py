"""Integration tests for the extraction cache layer."""

from __future__ import annotations

from pathlib import Path

from csegraph_core.index.cache import ExtractionCache
from csegraph_core.index.services import IndexService, RefreshService
from csegraph_core.languages.types import ParsedFile


class TestExtractionCache:
    def test_put_and_get(self, tmp_path):
        cache = ExtractionCache(str(tmp_path / "cache.db"))
        parsed = ParsedFile(
            rel_path="test.py",
            abs_path="/repo/test.py",
            sha256="abc123",
            mtime=1.0,
            size=100,
        )
        cache.put(parsed)
        result = cache.get("test.py", "abc123")
        assert result is not None
        assert result.rel_path == "test.py"
        assert result.sha256 == "abc123"
        assert cache.stats()["hits"] == 1
        assert cache.stats()["misses"] == 0
        cache.close()

    def test_miss_on_different_sha(self, tmp_path):
        cache = ExtractionCache(str(tmp_path / "cache.db"))
        parsed = ParsedFile(
            rel_path="test.py",
            abs_path="/repo/test.py",
            sha256="abc123",
            mtime=1.0,
            size=100,
        )
        cache.put(parsed)
        assert cache.get("test.py", "different") is None
        assert cache.stats()["hits"] == 0
        assert cache.stats()["misses"] == 1
        cache.close()

    def test_miss_on_absent_key(self, tmp_path):
        cache = ExtractionCache(str(tmp_path / "cache.db"))
        assert cache.get("nope.py", "abc") is None
        assert cache.stats()["misses"] == 1
        cache.close()

    def test_clear(self, tmp_path):
        cache = ExtractionCache(str(tmp_path / "cache.db"))
        parsed = ParsedFile(
            rel_path="test.py", abs_path="/repo/test.py",
            sha256="abc", mtime=1.0, size=10,
        )
        cache.put(parsed)
        assert cache.stats()["cached_files"] == 1
        cache.clear()
        assert cache.stats()["cached_files"] == 0
        cache.close()

    def test_preserves_symbols(self, tmp_path):
        from csegraph_core.languages.types import ParsedSymbol
        cache = ExtractionCache(str(tmp_path / "cache.db"))
        sym = ParsedSymbol(
            node_id="symbol::test.py::function::foo",
            kind="function", name="foo", file_path="test.py",
            start_line=1, end_line=3, signature="def foo()",
            docstring="Does stuff", source="def foo(): pass",
            source_hash="hash1",
        )
        parsed = ParsedFile(
            rel_path="test.py", abs_path="/repo/test.py",
            sha256="abc", mtime=1.0, size=10, symbols=[sym],
        )
        cache.put(parsed)
        result = cache.get("test.py", "abc")
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "foo"
        assert result.symbols[0].node_id == "symbol::test.py::function::foo"
        cache.close()


class TestCacheIntegration:
    def test_index_creates_cache(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def hello(): pass\n", encoding="utf-8")
        db = str(tmp_path / "index.db")
        result = IndexService(db).index(str(repo), profile="small")
        assert result.cache_hits == 0
        assert result.cache_misses == 1
        cache_path = tmp_path / "parse_cache.db"
        assert cache_path.exists()

    def test_second_index_uses_cache(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def hello(): pass\n", encoding="utf-8")
        db = str(tmp_path / "index.db")
        IndexService(db).index(str(repo), profile="small")
        result = IndexService(db).index(str(repo), profile="small")
        assert result.files_indexed >= 1
        assert result.cache_hits == 1
        assert result.cache_misses == 0

    def test_noop_refresh_reports_cache_stats(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def hello(): pass\n", encoding="utf-8")
        db = str(tmp_path / "index.db")
        IndexService(db).index(str(repo), profile="small")
        result = RefreshService(db).refresh(profile="small")
        assert result.files_indexed == 0
        assert result.cache_hits == 1
        assert result.cache_misses == 0
