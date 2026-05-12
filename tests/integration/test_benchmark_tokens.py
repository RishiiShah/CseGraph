"""Integration tests for P3 — token reduction benchmark."""

from __future__ import annotations

import math
from pathlib import Path

from csegraph_core.benchmark import BenchmarkService, _count_raw_tokens


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        'from helpers import fmt\n\ndef greet(name: str) -> str:\n    """Say hello."""\n    return fmt(name)\n',
        encoding="utf-8",
    )
    (repo / "helpers.py").write_text(
        'def fmt(name: str) -> str:\n    return f"Hello, {name}"\n',
        encoding="utf-8",
    )
    return repo


class TestCountRawTokens:
    def test_counts_all_files(self, tmp_path):
        repo = _make_repo(tmp_path)
        tokens = _count_raw_tokens(repo)
        app_text = (repo / "app.py").read_text(encoding="utf-8")
        helpers_text = (repo / "helpers.py").read_text(encoding="utf-8")
        expected = (
            max(1, math.ceil(len(app_text) / 4))
            + max(1, math.ceil(len(helpers_text) / 4))
        )
        assert tokens == expected

    def test_empty_repo(self, tmp_path):
        repo = tmp_path / "empty"
        repo.mkdir()
        assert _count_raw_tokens(repo) == 0


class TestBenchmarkTokenReduction:
    def test_has_token_reduction_step(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "bench.db")
        result = BenchmarkService(db).run(repo, profile="small")
        step_names = [s.name for s in result.steps]
        assert "token_reduction" in step_names

    def test_token_reduction_stats(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "bench.db")
        result = BenchmarkService(db).run(repo, profile="small")
        tr = next(s for s in result.steps if s.name == "token_reduction")
        assert "raw_tokens" in tr.stats
        assert "context_tokens" in tr.stats
        assert "reduction_percent" in tr.stats
        assert "ratio" in tr.stats
        assert tr.stats["raw_tokens"] > 0
        assert isinstance(tr.stats["reduction_percent"], float)

    def test_context_tokens_less_than_raw(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "bench.db")
        result = BenchmarkService(db).run(repo, profile="small")
        tr = next(s for s in result.steps if s.name == "token_reduction")
        assert tr.stats["context_tokens"] <= tr.stats["raw_tokens"]

    def test_reduction_percent_range(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "bench.db")
        result = BenchmarkService(db).run(repo, profile="small")
        tr = next(s for s in result.steps if s.name == "token_reduction")
        assert 0.0 <= tr.stats["reduction_percent"] <= 100.0

    def test_json_serializable(self, tmp_path):
        import json
        repo = _make_repo(tmp_path)
        db = str(tmp_path / "bench.db")
        result = BenchmarkService(db).run(repo, profile="small")
        from csegraph_core.core.models import to_dict
        payload = to_dict(result)
        serialized = json.dumps(payload)
        assert "token_reduction" in serialized
        assert "raw_tokens" in serialized
