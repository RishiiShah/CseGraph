from __future__ import annotations

import json
import math
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, TypeVar

from csegraph_core.core.models import BenchmarkResult, BenchmarkStep
from csegraph_core.core.serializer import to_dict


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
        expected_nodes: Iterable[str] | None = None,
        workflows: Iterable[dict] | None = None,
    ) -> BenchmarkResult:
        repo_root = str(Path(repo).resolve())
        output = str(
            Path(graph_output_path).resolve()
            if graph_output_path is not None
            else Path(self.db_path).resolve().with_name("csegraph-graph.html")
        )

        total_start = time.perf_counter()
        steps: list[BenchmarkStep] = []

        from csegraph_core.graph.report import ReportService
        from csegraph_core.graph.visual import VisualExportService
        from csegraph_core.index.services import IndexService, RefreshService
        from csegraph_core.retrieval.context import ContextService

        expected_node_ids = list(expected_nodes or [])

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
                    "phases": index_result.timings_ms,
                },
            )
        )

        refresh_result, elapsed = _time_call(
            lambda: RefreshService(self.db_path).refresh(profile=profile)
        )
        steps.append(
            BenchmarkStep(
                name="refresh",
                elapsed_ms=elapsed,
                stats={
                    "changed_files": len(refresh_result.changed_files),
                    "deleted_files": len(refresh_result.deleted_files),
                    "unchanged_files": len(refresh_result.unchanged_files),
                    "cache_hits": refresh_result.cache_hits,
                    "cache_misses": refresh_result.cache_misses,
                    "symbols": refresh_result.symbols_indexed,
                    "edges": refresh_result.edges_indexed,
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
        context_payload = to_dict(context_result)
        context_node_ids = {node.id for node in context_result.nodes}
        expected_node_hits = {
            node_id: node_id in context_node_ids
            for node_id in expected_node_ids
        }
        expected_node_hit_count = sum(1 for present in expected_node_hits.values() if present)
        expected_node_total = len(expected_node_hits)
        expected_node_hit_rate = (
            round(expected_node_hit_count / expected_node_total, 4)
            if expected_node_total
            else 1.0
        )
        steps.append(
            BenchmarkStep(
                name="context",
                elapsed_ms=elapsed,
                stats={
                    "nodes": len(context_result.nodes),
                    "schema_version": context_payload["schema_version"],
                    "detail_level": context_result.detail_level,
                    "returned_detail_level": context_result.returned_detail_level,
                    "total_estimated_tokens": context_result.total_estimated_tokens,
                    "sufficient": context_result.sufficiency.sufficient,
                    "target": context_result.target,
                    "mcp_response_bytes": len(
                        json.dumps(context_payload, sort_keys=True).encode("utf-8")
                    ),
                    "expected_nodes": expected_node_hits,
                    "expected_node_hit_count": expected_node_hit_count,
                    "expected_node_total": expected_node_total,
                    "expected_node_hit_rate": expected_node_hit_rate,
                    "missing_expected_nodes": [
                        node_id for node_id, present in expected_node_hits.items() if not present
                    ],
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

        raw_tokens, raw_elapsed = _time_call(
            lambda: _count_raw_tokens(Path(repo_root))
        )

        diff_tokens, diff_elapsed = _time_call(
            lambda: _count_diff_tokens(Path(repo_root))
        )

        context_with_source, ctx_elapsed = _time_call(
            lambda: ContextService(self.db_path).build_context(
                task=query,
                target=target,
                profile=profile,
                include_source="auto",
            )
        )
        context_tokens = context_with_source.total_estimated_tokens

        reduction_pct = (
            round((1 - context_tokens / raw_tokens) * 100, 2)
            if raw_tokens > 0
            else 0.0
        )
        naive_to_graph_ratio = (
            round(raw_tokens / context_tokens, 2)
            if context_tokens > 0
            else 0.0
        )
        diff_to_graph_ratio = (
            round(diff_tokens / context_tokens, 2)
            if context_tokens > 0 and diff_tokens > 0
            else 0.0
        )
        steps.append(
            BenchmarkStep(
                name="token_reduction",
                elapsed_ms=round(raw_elapsed + diff_elapsed + ctx_elapsed, 3),
                stats={
                    "raw_tokens": raw_tokens,
                    "diff_tokens": diff_tokens,
                    "context_tokens": context_tokens,
                    "reduction_percent": reduction_pct,
                    "naive_to_graph_ratio": naive_to_graph_ratio,
                    "diff_to_graph_ratio": diff_to_graph_ratio,
                    "ratio": f"{context_tokens}:{raw_tokens}",
                },
            )
        )

        # Optional custom workflow steps: run arbitrary tools/services and record response sizes.
        if workflows:
            from csegraph_core.retrieval.minimal import MinimalService
            from csegraph_core.graph.queries import GraphQueryService

            for wf in workflows:
                tool = wf.get("tool")
                args = wf.get("args", {}) or {}
                start = time.perf_counter()
                resp = None
                try:
                    if tool == "minimal":
                        resp = MinimalService(self.db_path).first(**args)
                    elif tool == "context":
                        resp = ContextService(self.db_path).build_context(**args)
                    elif tool == "graph":
                        resp = VisualExportService(self.db_path).export(args.get("output") or output)
                    elif tool == "path":
                        resp = GraphQueryService(self.db_path).shortest_path(**args)
                    elif tool == "index":
                        resp = IndexService(self.db_path).index(repo_root, profile=profile)
                    elif tool == "refresh":
                        resp = RefreshService(self.db_path).refresh(profile=profile)
                    else:
                        # Unsupported custom tool; skip
                        continue
                except Exception as exc:
                    elapsed = _elapsed_ms(start)
                    steps.append(BenchmarkStep(name=f"workflow:{tool}", elapsed_ms=elapsed, stats={"error": str(exc)}))
                    continue
                elapsed = _elapsed_ms(start)
                try:
                    payload = to_dict(resp)
                    resp_bytes = len(json.dumps(payload, sort_keys=True).encode("utf-8"))
                except Exception:
                    resp_bytes = 0
                steps.append(BenchmarkStep(name=f"workflow:{tool}", elapsed_ms=elapsed, stats={"mcp_response_bytes": resp_bytes}))

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


def _count_raw_tokens(repo_root: Path) -> int:
    total = 0
    from csegraph_core.languages.registry import registry
    for _parser, file_path in registry.iter_files(repo_root):
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
            total += max(1, math.ceil(len(text) / 2.7))
        except OSError:
            continue
    return total


def _count_diff_tokens(repo_root: Path) -> int:
    """Token count of `git diff HEAD` output. Returns 0 for non-git repos or git failures."""
    if not (repo_root / ".git").exists():
        return 0
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if completed.returncode != 0:
        return 0
    diff_text = completed.stdout
    if not diff_text:
        return 0
    return max(1, math.ceil(len(diff_text) / 2.7))
