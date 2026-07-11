"""Compatibility facade for the adaptive retrieval benchmark maintainer tools."""

from __future__ import annotations

from pathlib import Path

from tools.benchmarks.baseline import PyrightLspProvider, StrongBaselineAdapter, _lsp_locations
from tools.benchmarks.execution import execute_benchmark_task
from tools.benchmarks.models import (
    AdaptiveBenchmarkCorpus,
    AdaptiveBenchmarkTask,
    BenchmarkRepository,
)
from tools.benchmarks.quality import corpus_completeness, corpus_quality
from tools.benchmarks.schema import (
    corpus_to_payload,
    load_adaptive_corpus,
    load_adaptive_tasks,
)
from tools.benchmarks.workspace import (
    _fixture_revision,
    benchmark_workspace_hygiene,
    copy_benchmark_repository,
    prepare_benchmark_repository,
)


def build_adaptive_corpus(name: str, *, repo_root: Path) -> AdaptiveBenchmarkCorpus:
    """Build a named source-driven corpus without reading a manifest file."""

    from tools.benchmarks.corpora import build_adaptive_corpus as build

    return build(name, repo_root=repo_root)


__all__ = [
    "AdaptiveBenchmarkCorpus",
    "AdaptiveBenchmarkTask",
    "BenchmarkRepository",
    "PyrightLspProvider",
    "StrongBaselineAdapter",
    "benchmark_workspace_hygiene",
    "build_adaptive_corpus",
    "copy_benchmark_repository",
    "corpus_completeness",
    "corpus_quality",
    "corpus_to_payload",
    "execute_benchmark_task",
    "_fixture_revision",
    "_lsp_locations",
    "load_adaptive_corpus",
    "load_adaptive_tasks",
    "prepare_benchmark_repository",
]
