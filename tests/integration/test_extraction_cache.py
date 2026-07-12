"""Integration tests for the extraction cache layer."""

from __future__ import annotations

import contextlib
import sqlite3

import pytest

from csegraph._core.index import cache as cache_module
from csegraph._core.index.cache import ExtractionCache
from csegraph._core.index.services import IndexService, RefreshService
from csegraph._core.languages.types import ParsedFile


class TestExtractionCache:
    @staticmethod
    def _parsed(rel_path: str, sha256: str) -> ParsedFile:
        return ParsedFile(
            rel_path=rel_path,
            abs_path=f"/repo/{rel_path}",
            sha256=sha256,
            mtime=1.0,
            size=100,
        )

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
        cache_path = tmp_path / "cache.db"
        cache = ExtractionCache(str(cache_path))
        parsed = ParsedFile(
            rel_path="test.py",
            abs_path="/repo/test.py",
            sha256="abc",
            mtime=1.0,
            size=10,
        )
        cache.put(parsed)
        assert cache.stats()["cached_files"] == 1
        cache.clear()
        assert cache.stats()["cached_files"] == 0
        observer = sqlite3.connect(cache_path)
        assert observer.execute("SELECT COUNT(*) FROM parse_cache").fetchone()[0] == 0
        observer.close()
        cache.close()

    def test_preserves_symbols(self, tmp_path):
        from csegraph._core.languages.types import ParsedSymbol

        cache = ExtractionCache(str(tmp_path / "cache.db"))
        sym = ParsedSymbol(
            node_id="symbol::test.py::function::foo",
            kind="function",
            name="foo",
            file_path="test.py",
            start_line=1,
            end_line=3,
            signature="def foo()",
            docstring="Does stuff",
            source="def foo(): pass",
            source_hash="hash1",
        )
        parsed = ParsedFile(
            rel_path="test.py",
            abs_path="/repo/test.py",
            sha256="abc",
            mtime=1.0,
            size=10,
            symbols=[sym],
        )
        cache.put(parsed)
        result = cache.get("test.py", "abc")
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "foo"
        assert result.symbols[0].node_id == "symbol::test.py::function::foo"
        cache.close()

    def test_cache_version_mismatch_is_a_miss(self, monkeypatch, tmp_path):
        cache_path = tmp_path / "cache.db"
        cache = ExtractionCache(str(cache_path))
        parsed = ParsedFile(
            rel_path="test.py",
            abs_path="/repo/test.py",
            sha256="abc123",
            mtime=1.0,
            size=100,
        )
        cache.put(parsed)
        assert cache.get("test.py", "abc123") is not None
        cache.close()

        monkeypatch.setattr(
            cache_module,
            "CACHE_VERSION",
            f"{cache_module.CACHE_VERSION}-next",
        )
        changed_cache = ExtractionCache(str(cache_path))
        assert changed_cache.get("test.py", "abc123") is None
        assert changed_cache.stats()["misses"] == 1
        changed_cache.close()

    def test_batch_writes_commits_full_groups_and_clean_tail(self, tmp_path):
        cache_path = tmp_path / "cache.db"
        cache = ExtractionCache(cache_path)
        observer = sqlite3.connect(cache_path)

        with cache.batch_writes(max_pending=2):
            first = self._parsed("first.py", "first")
            cache.put(first)
            assert cache.get(first.rel_path, first.sha256) == first
            assert observer.execute("SELECT COUNT(*) FROM parse_cache").fetchone()[0] == 0

            cache.put(self._parsed("second.py", "second"))
            assert observer.execute("SELECT COUNT(*) FROM parse_cache").fetchone()[0] == 2

            cache.put(self._parsed("third.py", "third"))
            assert observer.execute("SELECT COUNT(*) FROM parse_cache").fetchone()[0] == 2

        assert observer.execute("SELECT COUNT(*) FROM parse_cache").fetchone()[0] == 3
        observer.close()
        cache.close()

    def test_batch_writes_rolls_back_only_uncommitted_tail(self, tmp_path):
        cache_path = tmp_path / "cache.db"
        cache = ExtractionCache(cache_path)

        with pytest.raises(RuntimeError, match="stop batch"):
            with cache.batch_writes(max_pending=2):
                cache.put(self._parsed("first.py", "first"))
                cache.put(self._parsed("second.py", "second"))
                cache.put(self._parsed("third.py", "third"))
                raise RuntimeError("stop batch")

        observer = sqlite3.connect(cache_path)
        cached_paths = {
            row[0] for row in observer.execute("SELECT rel_path FROM parse_cache").fetchall()
        }
        assert cached_paths == {"first.py", "second.py"}
        observer.close()
        cache.close()

    def test_batch_writes_rolls_back_tail_on_interrupt(self, tmp_path):
        cache_path = tmp_path / "cache.db"
        cache = ExtractionCache(cache_path)

        with pytest.raises(KeyboardInterrupt):
            with cache.batch_writes():
                cache.put(self._parsed("interrupted.py", "interrupted"))
                raise KeyboardInterrupt

        cache.put(self._parsed("after.py", "after"))
        observer = sqlite3.connect(cache_path)
        cached_paths = {
            row[0] for row in observer.execute("SELECT rel_path FROM parse_cache").fetchall()
        }
        assert cached_paths == {"after.py"}
        observer.close()
        cache.close()

    def test_batch_writes_rolls_back_when_tail_commit_fails(self, tmp_path):
        cache = ExtractionCache(tmp_path / "cache.db")
        real_connection = cache.conn

        class FailingCommitConnection:
            def __getattr__(self, name):
                return getattr(real_connection, name)

            def commit(self):
                raise sqlite3.OperationalError("injected commit failure")

        cache.conn = FailingCommitConnection()
        with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
            with cache.batch_writes():
                cache.put(self._parsed("failed.py", "failed"))

        assert real_connection.in_transaction is False
        cache.conn = real_connection
        assert cache.get("failed.py", "failed") is None
        cache.close()

    @pytest.mark.parametrize("max_pending", [0, -1])
    def test_batch_writes_rejects_non_positive_max_pending(self, tmp_path, max_pending):
        cache = ExtractionCache(tmp_path / "cache.db")

        with pytest.raises(ValueError, match="max_pending must be at least 1"):
            with cache.batch_writes(max_pending=max_pending):
                pass

        cache.close()

    def test_batch_writes_rejects_nested_batches(self, tmp_path):
        cache = ExtractionCache(tmp_path / "cache.db")

        with cache.batch_writes():
            with pytest.raises(RuntimeError, match="Nested cache write batches are not supported"):
                with cache.batch_writes():
                    pass

        cache.close()

    def test_put_outside_batch_is_immediately_durable(self, tmp_path):
        cache_path = tmp_path / "cache.db"
        cache = ExtractionCache(cache_path)
        cache.put(self._parsed("test.py", "abc123"))

        observer = sqlite3.connect(cache_path)
        assert observer.execute("SELECT COUNT(*) FROM parse_cache").fetchone()[0] == 1
        observer.close()
        cache.close()


class TestCacheIntegration:
    @staticmethod
    def _record_batches(monkeypatch):
        calls: list[int] = []
        real_batch = ExtractionCache.batch_writes

        @contextlib.contextmanager
        def recording_batch(cache, max_pending=100):
            calls.append(max_pending)
            with real_batch(cache, max_pending=max_pending):
                yield

        monkeypatch.setattr(ExtractionCache, "batch_writes", recording_batch)
        return calls

    def test_index_batches_cache_writes(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def hello(): pass\n", encoding="utf-8")
        calls = self._record_batches(monkeypatch)

        IndexService(tmp_path / "index.db").index(repo)

        assert calls == [100]

    def test_refresh_batches_changed_cache_writes(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        source = repo / "app.py"
        source.write_text("def hello(): return 1\n", encoding="utf-8")
        db = tmp_path / "index.db"
        IndexService(db).index(repo)
        calls = self._record_batches(monkeypatch)
        source.write_text("def hello(): return 2\n", encoding="utf-8")

        RefreshService(db).refresh(changed_paths=[source], dependents_limit=0)

        assert calls == [100]

    def test_refresh_batches_dependent_cache_writes(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        child = repo / "child.py"
        child.write_text("def helper(): return 1\n", encoding="utf-8")
        (repo / "caller.py").write_text(
            "from child import helper\n\ndef caller(): return helper()\n",
            encoding="utf-8",
        )
        db = tmp_path / "index.db"
        IndexService(db).index(repo)
        calls = self._record_batches(monkeypatch)
        child.write_text("def helper(): return 2\n", encoding="utf-8")

        result = RefreshService(db).refresh(changed_paths=[child])

        assert result.dependents_expanded == 1
        assert calls == [100, 100]

    def test_index_creates_cache(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def hello(): pass\n", encoding="utf-8")
        db = str(tmp_path / "index.db")
        result = IndexService(db).index(str(repo))
        assert result.cache_hits == 0
        assert result.cache_misses == 1
        cache_path = tmp_path / "parse_cache.db"
        assert cache_path.exists()

    def test_second_index_uses_cache(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def hello(): pass\n", encoding="utf-8")
        db = str(tmp_path / "index.db")
        IndexService(db).index(str(repo))
        result = IndexService(db).index(str(repo))
        assert result.files_indexed >= 1
        assert result.cache_hits == 1
        assert result.cache_misses == 0

    def test_noop_refresh_reports_cache_stats(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def hello(): pass\n", encoding="utf-8")
        db = str(tmp_path / "index.db")
        IndexService(db).index(str(repo))
        result = RefreshService(db).refresh()
        assert result.files_indexed == 0
        assert result.cache_hits == 1
        assert result.cache_misses == 0
