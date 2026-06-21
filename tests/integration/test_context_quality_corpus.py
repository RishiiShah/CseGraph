"""Regression guard for context-quality benchmark corpus on this repository."""

from __future__ import annotations

from pathlib import Path

from csegraph._core.benchmark import BenchmarkService

_ROOT = Path(__file__).resolve().parents[2]
_CORPUS = _ROOT / "benchmarks" / "context_quality" / "csegraph_self.json"

# Baselines from csegraph self-index (profile=small); tighten only with intent.
_MIN_OVERALL_HIT_RATE = 0.9
_MIN_TASK_PASS_RATE = 0.8
_MAX_AVG_CONTEXT_TOKENS = 1500
_MAX_AVG_RESPONSE_BYTES = 29000
_MAX_RETURNED_NODE_COUNT = 16
_MIN_TASK_HIT_RATE = 0.70


def test_context_quality_corpus_hit_rate(tmp_path):
    db_path = tmp_path / "corpus.db"
    result = BenchmarkService(str(db_path)).run_corpus(
        _ROOT,
        _CORPUS,
        profile="small",
    )
    summary = result.summary

    assert summary.task_count == 5
    assert summary.overall_hit_rate >= _MIN_OVERALL_HIT_RATE
    assert summary.task_pass_rate >= _MIN_TASK_PASS_RATE
    assert summary.sufficient_task_count == 5
    assert summary.avg_context_tokens <= _MAX_AVG_CONTEXT_TOKENS
    assert summary.avg_response_bytes <= _MAX_AVG_RESPONSE_BYTES

    for task in result.tasks:
        assert task.error is None
        assert task.hit_rate >= _MIN_TASK_HIT_RATE, task.task_id
        assert task.returned_node_count <= _MAX_RETURNED_NODE_COUNT, task.task_id
