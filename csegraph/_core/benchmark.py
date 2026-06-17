from __future__ import annotations

import json
import math
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

from csegraph._core.core.models import (
    BenchmarkCorpusResult,
    BenchmarkCorpusSummary,
    BenchmarkCorpusTask,
    BenchmarkCorpusTaskResult,
    BenchmarkResult,
    BenchmarkStep,
)
from csegraph._core.core.serializer import to_dict


_DEFAULT_QUERY = "Benchmark context retrieval"
_CORPUS_SCHEMA_VERSION = "csegraph-context-benchmark-v1"
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

        from csegraph._core.graph.report import ReportService
        from csegraph._core.graph.visual import VisualExportService
        from csegraph._core.index.services import IndexService, RefreshService
        from csegraph._core.retrieval.context import ContextService

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
            from csegraph._core.retrieval.minimal import MinimalService
            from csegraph._core.graph.queries import GraphQueryService

            for wf in workflows:
                tool = wf.get("tool")
                args = wf.get("args", {}) or {}
                start = time.perf_counter()
                resp: Any = None
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

    def run_corpus(
        self,
        repo: str | Path,
        corpus_path: str | Path,
        *,
        profile: str = "medium",
    ) -> BenchmarkCorpusResult:
        repo_root = str(Path(repo).resolve())
        corpus_file = Path(corpus_path).resolve()
        tasks = _load_corpus(corpus_file)

        total_start = time.perf_counter()

        from csegraph._core.index.services import IndexService
        from csegraph._core.retrieval.context import ContextService

        index_result, index_elapsed = _time_call(
            lambda: IndexService(self.db_path).index(repo_root, profile=profile)
        )
        index_stats = {
            "files": index_result.files_indexed,
            "symbols": index_result.symbols_indexed,
            "edges": index_result.edges_indexed,
            "parse_errors": len(index_result.parse_errors),
            "elapsed_ms": index_elapsed,
            "phases": index_result.timings_ms,
        }

        task_results: list[BenchmarkCorpusTaskResult] = []
        for task in tasks:
            task_results.append(
                _run_corpus_task(
                    ContextService(self.db_path),
                    task,
                    profile=profile,
                )
            )

        summary = _summarize_corpus(task_results)
        return BenchmarkCorpusResult(
            command="benchmark-corpus",
            db_path=self.db_path,
            repo_root=repo_root,
            profile=profile,
            corpus_path=str(corpus_file),
            total_elapsed_ms=_elapsed_ms(total_start),
            index_stats=index_stats,
            summary=summary,
            tasks=task_results,
        )

    def run_agent_workflows(
        self,
        repo: str | Path,
        *,
        profile: str = "medium",
    ) -> BenchmarkResult:
        """Benchmark multi-step MCP workflows used for code-change tasks."""
        from csegraph._core.benchmark_workflows import run_agent_workflow_benchmarks
        from csegraph._core.index.services import IndexService
        from csegraph._core.server.app import _handle_tool
        from csegraph._core.server.session import _SESSION

        repo_root = str(Path(repo).resolve())

        def ensure_indexed() -> None:
            IndexService(self.db_path).index(repo_root, profile=profile)

        return run_agent_workflow_benchmarks(
            repo_root,
            self.db_path,
            profile=profile,
            handle_tool=_handle_tool,
            reset_session=_SESSION.reset,
            ensure_indexed=ensure_indexed,
        )


def _time_call(callback: Callable[[], _T]) -> tuple[_T, float]:
    start = time.perf_counter()
    result = callback()
    return result, _elapsed_ms(start)


def _load_corpus(path: Path) -> list[BenchmarkCorpusTask]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"Benchmark corpus not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Benchmark corpus is not valid JSON: {path}") from exc

    schema_version = payload.get("schema_version")
    if schema_version != _CORPUS_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported benchmark corpus schema_version {schema_version!r}. "
            f"Expected {_CORPUS_SCHEMA_VERSION!r}."
        )

    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("Benchmark corpus must contain at least one task.")

    tasks: list[BenchmarkCorpusTask] = []
    for index, raw_task in enumerate(raw_tasks, start=1):
        if not isinstance(raw_task, dict):
            raise ValueError(f"Benchmark corpus task {index} must be an object.")
        task_id = _required_non_empty_string(raw_task, "id", index)
        query = _required_non_empty_string(raw_task, "query", index)
        target = raw_task.get("target")
        if target is not None:
            if not isinstance(target, str):
                raise ValueError(f"Benchmark corpus task {task_id!r} target must be a string.")
            target = target.strip() or None

        expected_nodes = _string_list(raw_task, "expected_nodes", task_id)
        expected_files = [_normalize_rel_path(p) for p in _string_list(raw_task, "expected_files", task_id)]
        expected_symbols = _string_list(raw_task, "expected_symbols", task_id)

        if not (expected_nodes or expected_files or expected_symbols):
            raise ValueError(
                f"Benchmark corpus task {task_id!r} must define at least one of "
                "expected_nodes, expected_files, or expected_symbols."
            )
        tasks.append(
            BenchmarkCorpusTask(
                id=task_id,
                query=query,
                target=target,
                expected_nodes=expected_nodes,
                expected_files=expected_files,
                expected_symbols=expected_symbols,
            )
        )
    return tasks


def _required_non_empty_string(raw_task: dict, field: str, index: int) -> str:
    value = raw_task.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Benchmark corpus task {index} must define a non-empty {field}.")
    return value.strip()


def _string_list(raw_task: dict, field: str, task_id: str) -> list[str]:
    value = raw_task.get(field, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"Benchmark corpus task {task_id!r} field {field} must be a list.")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"Benchmark corpus task {task_id!r} field {field} must contain non-empty strings."
            )
        result.append(item.strip())
    return result


def _run_corpus_task(
    service,
    task: BenchmarkCorpusTask,
    *,
    profile: str,
) -> BenchmarkCorpusTaskResult:
    try:
        context = service.build_context(
            task=task.query,
            target=task.target,
            profile=profile,
            include_source="never",
        )
    except Exception as exc:
        expected_total = (
            len(task.expected_nodes)
            + len(task.expected_files)
            + len(task.expected_symbols)
        )
        return BenchmarkCorpusTaskResult(
            task_id=task.id,
            query=task.query,
            target=task.target,
            returned_target=None,
            returned_detail_level=None,
            sufficient=False,
            returned_node_count=0,
            context_tokens=0,
            response_bytes=0,
            tool_call_count=1,
            hit_rate=0.0,
            node_hit_rate=0.0 if task.expected_nodes else 1.0,
            file_hit_rate=0.0 if task.expected_files else 1.0,
            symbol_hit_rate=0.0 if task.expected_symbols else 1.0,
            expected_node_total=len(task.expected_nodes),
            expected_file_total=len(task.expected_files),
            expected_symbol_total=len(task.expected_symbols),
            expected_hit_count=0,
            expected_total=expected_total,
            missing_expected_nodes=list(task.expected_nodes),
            missing_expected_files=list(task.expected_files),
            missing_expected_symbols=list(task.expected_symbols),
            error=str(exc),
        )

    payload = to_dict(context)
    response_bytes = len(json.dumps(payload, sort_keys=True).encode("utf-8"))
    returned_ids = {node.id for node in context.nodes}
    returned_files = {_normalize_rel_path(node.path) for node in context.nodes}
    returned_symbols = _returned_symbol_names(context.nodes)

    missing_nodes = [node_id for node_id in task.expected_nodes if node_id not in returned_ids]
    missing_files = [path for path in task.expected_files if _normalize_rel_path(path) not in returned_files]
    missing_symbols = [
        symbol for symbol in task.expected_symbols
        if symbol not in returned_symbols
    ]

    node_hits = len(task.expected_nodes) - len(missing_nodes)
    file_hits = len(task.expected_files) - len(missing_files)
    symbol_hits = len(task.expected_symbols) - len(missing_symbols)
    expected_total = (
        len(task.expected_nodes)
        + len(task.expected_files)
        + len(task.expected_symbols)
    )
    expected_hit_count = node_hits + file_hits + symbol_hits

    return BenchmarkCorpusTaskResult(
        task_id=task.id,
        query=task.query,
        target=task.target,
        returned_target=context.target,
        returned_detail_level=context.returned_detail_level,
        sufficient=context.sufficiency.sufficient,
        returned_node_count=len(context.nodes),
        context_tokens=context.total_estimated_tokens,
        response_bytes=response_bytes,
        tool_call_count=1,
        hit_rate=_rate(expected_hit_count, expected_total),
        node_hit_rate=_rate(node_hits, len(task.expected_nodes)),
        file_hit_rate=_rate(file_hits, len(task.expected_files)),
        symbol_hit_rate=_rate(symbol_hits, len(task.expected_symbols)),
        expected_node_total=len(task.expected_nodes),
        expected_file_total=len(task.expected_files),
        expected_symbol_total=len(task.expected_symbols),
        expected_hit_count=expected_hit_count,
        expected_total=expected_total,
        missing_expected_nodes=missing_nodes,
        missing_expected_files=missing_files,
        missing_expected_symbols=missing_symbols,
        error=None,
    )


def _returned_symbol_names(nodes) -> set[str]:
    names: set[str] = set()
    for node in nodes:
        names.add(node.name)
        if "." in node.name:
            names.add(node.name.rsplit(".", 1)[-1])
    return names


def _summarize_corpus(tasks: list[BenchmarkCorpusTaskResult]) -> BenchmarkCorpusSummary:
    task_count = len(tasks)
    passed = sum(1 for task in tasks if task.error is None and task.hit_rate == 1.0)
    total_expected = sum(task.expected_total for task in tasks)
    total_hits = sum(task.expected_hit_count for task in tasks)
    total_tokens = sum(task.context_tokens for task in tasks)
    total_bytes = sum(task.response_bytes for task in tasks)
    total_tool_calls = sum(task.tool_call_count for task in tasks)
    return BenchmarkCorpusSummary(
        task_count=task_count,
        passed_task_count=passed,
        failed_task_count=task_count - passed,
        overall_hit_rate=_rate(total_hits, total_expected),
        task_pass_rate=_rate(passed, task_count),
        total_context_tokens=total_tokens,
        avg_context_tokens=round(total_tokens / task_count, 2) if task_count else 0.0,
        total_response_bytes=total_bytes,
        avg_response_bytes=round(total_bytes / task_count, 2) if task_count else 0.0,
        total_tool_call_count=total_tool_calls,
    )


def _rate(hits: int, total: int) -> float:
    if total <= 0:
        return 1.0
    return round(hits / total, 4)


def _normalize_rel_path(path: str) -> str:
    return Path(path).as_posix().lstrip("./")


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def _file_size(path: str | Path) -> int:
    output = Path(path)
    return output.stat().st_size if output.exists() else 0


def _count_raw_tokens(repo_root: Path) -> int:
    total = 0
    from csegraph._core.languages.registry import registry
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
