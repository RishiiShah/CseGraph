from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, TypeVar

from csegraph_core.core.models import BenchmarkResult, BenchmarkStep
from csegraph_core.graph.report import ReportService
from csegraph_core.graph.visual import VisualExportService
from csegraph_core.index.services import IndexService
from csegraph_core.retrieval.context import ContextService


_DEFAULT_QUERY = "Benchmark context retrieval"
_T = TypeVar("_T")


class BenchmarkService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def run(
        self,
        repo: str | Path,
        *,
        profile: str = "medium",
        query: str = _DEFAULT_QUERY,
        target: str | None = None,
        graph_output_path: str | Path | None = None,
    ) -> BenchmarkResult:
        repo_root = str(Path(repo).resolve())
        output = str(
            Path(graph_output_path).resolve()
            if graph_output_path is not None
            else Path(self.db_path).resolve().with_name("csegraph-graph.html")
        )

        total_start = time.perf_counter()
        steps: list[BenchmarkStep] = []

        index_result, elapsed = _time_call(
            lambda: IndexService(self.db_path).index(repo_root, profile=profile)
        )
        steps.append(
            BenchmarkStep(
                name="index",
                elapsed_ms=elapsed,
                stats={
                    "files": index_result.files_indexed,
                    "symbols": index_result.symbols_indexed,
                    "edges": index_result.edges_indexed,
                    "parse_errors": len(index_result.parse_errors),
                },
            )
        )

        context_result, elapsed = _time_call(
            lambda: ContextService(self.db_path).build_context(
                task=query,
                target=target,
                profile=profile,
                include_source="never",
            )
        )
        steps.append(
            BenchmarkStep(
                name="context",
                elapsed_ms=elapsed,
                stats={
                    "nodes": len(context_result.nodes),
                    "total_estimated_tokens": context_result.total_estimated_tokens,
                    "sufficient": context_result.sufficiency.sufficient,
                    "target": context_result.target,
                },
            )
        )

        graph_result, elapsed = _time_call(
            lambda: VisualExportService(self.db_path).export(output)
        )
        steps.append(
            BenchmarkStep(
                name="graph",
                elapsed_ms=elapsed,
                stats={
                    "nodes": graph_result.total_nodes,
                    "edges": graph_result.total_edges,
                    "output_path": graph_result.output_path,
                    "output_size_bytes": _file_size(graph_result.output_path),
                },
            )
        )

        report_result, elapsed = _time_call(
            lambda: ReportService(self.db_path).report()
        )
        steps.append(
            BenchmarkStep(
                name="report",
                elapsed_ms=elapsed,
                stats={
                    "files": report_result.total_files,
                    "symbols": report_result.total_symbols,
                    "edges": report_result.total_edges,
                    "knowledge_gaps": len(report_result.knowledge_gaps),
                    "surprising_connections": len(report_result.surprising_connections),
                },
            )
        )

        return BenchmarkResult(
            command="benchmark",
            db_path=self.db_path,
            repo_root=repo_root,
            profile=profile,
            query=query,
            target=target,
            graph_output_path=output,
            total_elapsed_ms=_elapsed_ms(total_start),
            steps=steps,
        )


def _time_call(callback: Callable[[], _T]) -> tuple[_T, float]:
    start = time.perf_counter()
    result = callback()
    return result, _elapsed_ms(start)


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def _file_size(path: str | Path) -> int:
    output = Path(path)
    return output.stat().st_size if output.exists() else 0
