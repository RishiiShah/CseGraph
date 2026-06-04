"""Regression guard for context-quality benchmark corpus on this repository."""
from __future__ import annotations

from pathlib import Path

from csegraph._core.benchmark import BenchmarkService

_ROOT = Path(__file__).resolve().parents[2]
_CORPUS = _ROOT / "benchmarks" / "context_quality" / "csegraph_self.json"

# Baselines from csegraph self-index (profile=small); tighten only with intent.
_MIN_OVERALL_HIT_RATE = 0.85
_MIN_TASK_PASS_RATE = 0.66


def test_context_quality_corpus_hit_rate(tmp_path):
    db_path = tmp_path / "corpus.db"
    result = BenchmarkService(str(db_path)).run_corpus(
        _ROOT,
        _CORPUS,
        profile="small",
    )
    summary = result.summary

    assert summary.task_count == 3
    assert summary.overall_hit_rate >= _MIN_OVERALL_HIT_RATE
    assert summary.task_pass_rate >= _MIN_TASK_PASS_RATE
    assert summary.failed_task_count <= 1

    for task in result.tasks:
        assert task.error is None
        assert task.hit_rate >= 0.75, task.task_id
