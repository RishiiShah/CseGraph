from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from csegraph._core.benchmark import (
    _collect_context_strings,
    _collect_visible_source_strings,
    _contains_substring,
    _load_corpus,
    _normalize_rel_path,
    _rate,
    _relationship_label,
    _relationship_signature,
    _returned_symbol_names,
    _summarize_corpus,
)
from csegraph._core.core.models import (
    BenchmarkCorpusResult,
    BenchmarkCorpusTaskResult,
    ContextNode,
    ContextRelationship,
    ContextResult,
    ImportPrelude,
    RelationshipOccurrence,
    SufficiencyResult,
)
from csegraph._core.core.serializer import to_dict
from csegraph._core.cse.metrics import SufficiencyMetrics

DEFAULT_MIN_OVERALL_HIT_RATE = 1.0
DEFAULT_MIN_TASK_PASS_RATE = 1.0
DEFAULT_MAX_FAILED_TASKS = 0
DEFAULT_MAX_AVG_CONTEXT_TOKENS = 1500
DEFAULT_MAX_AVG_RESPONSE_BYTES = 29000
DEFAULT_MAX_RETURNED_NODE_COUNT = 16
DEFAULT_MIN_TASK_HIT_RATE = 0.70
DEFAULT_MIN_SUFFICIENT_TASK_RATE = 1.0


class NativeMcpClient:
    def __init__(self, command: str, args: Sequence[str], *, cwd: Path) -> None:
        self.command = command
        self.args = list(args)
        self.cwd = cwd
        self._stdio_cm: Any = None
        self._session_cm: Any = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "NativeMcpClient":
        params = StdioServerParameters(
            command=self.command,
            args=self.args,
            cwd=self.cwd,
            env=dict(os.environ),
        )
        self._stdio_cm = stdio_client(params)
        read_stream, write_stream = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read_stream, write_stream)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._session_cm is not None:
            await self._session_cm.__aexit__(exc_type, exc, tb)
        if self._stdio_cm is not None:
            await self._stdio_cm.__aexit__(exc_type, exc, tb)

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError("MCP session is not initialized")
        result = await self._session.call_tool(tool, arguments=arguments)
        text = _extract_content_text(result)
        if getattr(result, "isError", False):
            raise RuntimeError(f"{tool} failed over MCP: {text}")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{tool} returned non-JSON MCP text: {text[:300]}") from exc
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(f"{tool} failed over MCP: {payload['error']}")
        if not isinstance(payload, dict):
            raise RuntimeError(f"{tool} returned unexpected payload type: {type(payload).__name__}")
        return payload


class McpContextService:
    def __init__(self, client: NativeMcpClient, repo: Path, db_path: Path):
        self.client = client
        self.repo = repo
        self.db_path = db_path

    async def build_context_async(
        self,
        *,
        task: str,
        target: str | None = None,
        profile: str,
        include_source: str = "never",
        detail_level: str = "auto",
        max_tokens: int | None = None,
    ) -> ContextResult:
        arguments: dict[str, Any] = {
            "repo": str(self.repo),
            "db": str(self.db_path),
            "task": task,
            "profile": profile,
            "include_source": include_source,
            "detail_level": detail_level,
        }
        if target is not None:
            arguments["target"] = target
        if max_tokens is not None:
            arguments["max_tokens"] = max_tokens
        payload = await self.client.call_tool("csegraph_context", arguments)
        return _context_from_payload(payload)

    def build_context(self, **kwargs: Any) -> ContextResult:
        raise RuntimeError("Use run_mcp_corpus; synchronous SDK context calls are disabled here.")


async def main_async(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo = Path(args.repo).resolve()
    corpus = Path(args.corpus).resolve()
    tasks = _load_corpus(corpus)
    command, server_args = _server_command(args)

    scratch_root = repo / ".scratch" / "csegraph"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="benchmark-mcp-", dir=scratch_root) as tmp:
        db_path = Path(args.db).resolve() if args.db else Path(tmp) / "benchmark.db"
        async with NativeMcpClient(command, server_args, cwd=repo) as client:
            total_start = time.perf_counter()
            index_start = time.perf_counter()
            index_payload = await client.call_tool(
                "csegraph_index",
                {
                    "repo": str(repo),
                    "db": str(db_path),
                    "profile": args.profile,
                    "postprocess_level": args.postprocess_level,
                },
            )
            index_elapsed = _elapsed_ms(index_start)
            context_service = McpContextService(client, repo, db_path)
            task_results = []
            for task in tasks:
                task_results.append(
                    await _run_mcp_corpus_task(context_service, task, profile=args.profile)
                )
            summary = _summarize_corpus(task_results)
            result = BenchmarkCorpusResult(
                command="benchmark-corpus-mcp",
                db_path=str(db_path),
                repo_root=str(repo),
                profile=args.profile,
                corpus_path=str(corpus),
                total_elapsed_ms=_elapsed_ms(total_start),
                index_stats=_index_stats(index_payload, index_elapsed),
                summary=summary,
                tasks=task_results,
            )

    payload = to_dict(result)
    print(json.dumps(payload, indent=2, sort_keys=True))

    violations = _threshold_violations(payload, args)
    if violations:
        print("Benchmark regression thresholds failed:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1
    return 0


async def _run_mcp_corpus_task(
    service: McpContextService,
    task: Any,
    *,
    profile: str,
) -> Any:
    try:
        context = await service.build_context_async(
            task=task.query,
            target=task.target,
            profile=profile,
            include_source=task.include_source,
            detail_level=task.detail_level,
            max_tokens=task.max_tokens,
        )
    except Exception as exc:
        expected_total = (
            len(task.expected_nodes)
            + len(task.expected_files)
            + len(task.expected_symbols)
            + len(task.expected_relationships)
            + len(task.expected_occurrence_snippets)
            + len(task.expected_import_preludes)
            + len(task.forbidden_source_patterns)
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
            relationship_hit_rate=0.0 if task.expected_relationships else 1.0,
            occurrence_snippet_hit_rate=0.0 if task.expected_occurrence_snippets else 1.0,
            import_prelude_hit_rate=0.0 if task.expected_import_preludes else 1.0,
            forbidden_source_pattern_hit_rate=0.0 if task.forbidden_source_patterns else 1.0,
            expected_node_total=len(task.expected_nodes),
            expected_file_total=len(task.expected_files),
            expected_symbol_total=len(task.expected_symbols),
            expected_relationship_total=len(task.expected_relationships),
            expected_occurrence_snippet_total=len(task.expected_occurrence_snippets),
            expected_import_prelude_total=len(task.expected_import_preludes),
            forbidden_source_pattern_total=len(task.forbidden_source_patterns),
            expected_hit_count=0,
            expected_total=expected_total,
            missing_expected_nodes=list(task.expected_nodes),
            missing_expected_files=list(task.expected_files),
            missing_expected_symbols=list(task.expected_symbols),
            missing_expected_relationships=[
                _relationship_label(relationship) for relationship in task.expected_relationships
            ],
            missing_expected_occurrence_snippets=list(task.expected_occurrence_snippets),
            missing_expected_import_preludes=list(task.expected_import_preludes),
            violating_forbidden_source_patterns=list(task.forbidden_source_patterns),
            error=str(exc),
        )

    payload = to_dict(context)
    response_bytes = len(json.dumps(payload, sort_keys=True).encode("utf-8"))
    returned_ids = {node.id for node in context.nodes}
    returned_files = {_normalize_rel_path(node.path) for node in context.nodes}
    returned_symbols = _returned_symbol_names(context.nodes)
    returned_relationships = {
        _relationship_signature(
            {
                "source": relationship.source,
                "relation": relationship.relation,
                "target": relationship.target,
            }
        )
        for relationship in context.relationships
    }
    relationship_strings = _collect_context_strings(context.relationships)
    import_prelude_texts = [prelude.text for prelude in context.import_preludes if prelude.text]
    visible_source_strings = _collect_visible_source_strings(context)
    tool_call_count = 1

    missing_nodes = [node_id for node_id in task.expected_nodes if node_id not in returned_ids]
    missing_files = [
        path for path in task.expected_files if _normalize_rel_path(path) not in returned_files
    ]
    missing_symbols = [symbol for symbol in task.expected_symbols if symbol not in returned_symbols]
    missing_relationships = [
        _relationship_label(relationship)
        for relationship in task.expected_relationships
        if _relationship_signature(relationship) not in returned_relationships
    ]
    missing_occurrence_snippets = [
        snippet
        for snippet in task.expected_occurrence_snippets
        if not _contains_substring(relationship_strings, snippet)
    ]
    if missing_occurrence_snippets:
        try:
            occurrence_context = await service.build_context_async(
                task=task.query,
                target=task.target,
                profile=profile,
                include_source="auto",
                detail_level="standard",
            )
            tool_call_count += 1
            relationship_strings.extend(_collect_context_strings(occurrence_context.relationships))
            missing_occurrence_snippets = [
                snippet
                for snippet in task.expected_occurrence_snippets
                if not _contains_substring(relationship_strings, snippet)
            ]
        except Exception:
            pass
    missing_import_preludes = [
        snippet
        for snippet in task.expected_import_preludes
        if not _contains_substring(import_prelude_texts, snippet)
    ]
    violating_forbidden_source_patterns = [
        pattern
        for pattern in task.forbidden_source_patterns
        if _contains_substring(visible_source_strings, pattern)
    ]

    node_hits = len(task.expected_nodes) - len(missing_nodes)
    file_hits = len(task.expected_files) - len(missing_files)
    symbol_hits = len(task.expected_symbols) - len(missing_symbols)
    relationship_hits = len(task.expected_relationships) - len(missing_relationships)
    occurrence_hits = len(task.expected_occurrence_snippets) - len(missing_occurrence_snippets)
    import_prelude_hits = len(task.expected_import_preludes) - len(missing_import_preludes)
    forbidden_pattern_hits = len(task.forbidden_source_patterns) - len(
        violating_forbidden_source_patterns
    )
    expected_total = (
        len(task.expected_nodes)
        + len(task.expected_files)
        + len(task.expected_symbols)
        + len(task.expected_relationships)
        + len(task.expected_occurrence_snippets)
        + len(task.expected_import_preludes)
        + len(task.forbidden_source_patterns)
    )
    expected_hit_count = (
        node_hits
        + file_hits
        + symbol_hits
        + relationship_hits
        + occurrence_hits
        + import_prelude_hits
        + forbidden_pattern_hits
    )

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
        tool_call_count=tool_call_count,
        hit_rate=_rate(expected_hit_count, expected_total),
        node_hit_rate=_rate(node_hits, len(task.expected_nodes)),
        file_hit_rate=_rate(file_hits, len(task.expected_files)),
        symbol_hit_rate=_rate(symbol_hits, len(task.expected_symbols)),
        relationship_hit_rate=_rate(relationship_hits, len(task.expected_relationships)),
        occurrence_snippet_hit_rate=_rate(occurrence_hits, len(task.expected_occurrence_snippets)),
        import_prelude_hit_rate=_rate(import_prelude_hits, len(task.expected_import_preludes)),
        forbidden_source_pattern_hit_rate=_rate(
            forbidden_pattern_hits, len(task.forbidden_source_patterns)
        ),
        expected_node_total=len(task.expected_nodes),
        expected_file_total=len(task.expected_files),
        expected_symbol_total=len(task.expected_symbols),
        expected_relationship_total=len(task.expected_relationships),
        expected_occurrence_snippet_total=len(task.expected_occurrence_snippets),
        expected_import_prelude_total=len(task.expected_import_preludes),
        forbidden_source_pattern_total=len(task.forbidden_source_patterns),
        expected_hit_count=expected_hit_count,
        expected_total=expected_total,
        missing_expected_nodes=missing_nodes,
        missing_expected_files=missing_files,
        missing_expected_symbols=missing_symbols,
        missing_expected_relationships=missing_relationships,
        missing_expected_occurrence_snippets=missing_occurrence_snippets,
        missing_expected_import_preludes=missing_import_preludes,
        violating_forbidden_source_patterns=violating_forbidden_source_patterns,
        error=None,
    )


def _extract_content_text(result: Any) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(text)
        elif hasattr(item, "model_dump_json"):
            parts.append(item.model_dump_json())
        else:
            parts.append(str(item))
    return "\n".join(parts)


def _context_from_payload(payload: dict[str, Any]) -> ContextResult:
    request = payload.get("request") or {}
    target = payload.get("target") or {}
    budgets = payload.get("budgets") or {}
    sufficiency = payload.get("sufficiency") or {}
    return ContextResult(
        command=str(payload.get("command") or "context"),
        db_path=str(request.get("db_path") or ""),
        repo_root=str(payload.get("repo_root") or ""),
        profile=str(request.get("profile") or ""),
        query=str(request.get("task") or ""),
        target=str(target.get("id") or ""),
        detail_level=str(request.get("detail_level") or "auto"),
        returned_detail_level=str(request.get("returned_detail_level") or "auto"),
        sufficiency=_sufficiency_from_payload(sufficiency),
        total_estimated_tokens=int(budgets.get("total_estimated_tokens") or 0),
        nodes=[_node_from_payload(node) for node in payload.get("symbols") or []],
        relationships=[
            _relationship_from_payload(relationship)
            for relationship in payload.get("relationships") or []
        ],
        import_preludes=[
            _import_prelude_from_payload(prelude)
            for prelude in payload.get("import_preludes") or []
        ],
        target_input=request.get("target_input"),
        source_policy=str(request.get("source_policy") or "auto"),
        raw_code_nodes=list(budgets.get("raw_code_nodes") or []),
        next_actions=list(payload.get("next_actions") or []),
        warnings=list(payload.get("warnings") or []),
        run_id=payload.get("run_id"),
        confidence_breakdown=dict(payload.get("confidence_breakdown") or {}),
        target_resolution=str(target.get("resolution") or "resolved"),
        target_candidates=list(target.get("candidates") or []),
    )


def _node_from_payload(payload: dict[str, Any]) -> ContextNode:
    return ContextNode(
        id=str(payload.get("id") or ""),
        kind=str(payload.get("kind") or ""),
        name=str(payload.get("name") or ""),
        language=str(payload.get("language") or "text"),
        path=str(payload.get("path") or ""),
        line_range=payload.get("line_range"),
        score=float(payload.get("score") or 0.0),
        summary=str(payload.get("summary") or ""),
        source_text=payload.get("source_text"),
        estimated_tokens=int(payload.get("estimated_tokens") or 0),
        reason=list(payload.get("reason") or []),
        reason_details=list(payload.get("reason_details") or []),
        explanation=payload.get("explanation"),
        source_omitted_reason=payload.get("source_omitted_reason"),
    )


def _relationship_from_payload(payload: dict[str, Any]) -> ContextRelationship:
    return ContextRelationship(
        source=str(payload.get("source") or ""),
        target=str(payload.get("target") or ""),
        relation=str(payload.get("relation") or ""),
        metadata=dict(payload.get("metadata") or {}),
        occurrences=[
            _occurrence_from_payload(occurrence) for occurrence in payload.get("occurrences") or []
        ],
        confidence=float(payload.get("confidence") or 1.0),
        confidence_tier=str(payload.get("confidence_tier") or "EXTRACTED"),
        source_path=payload.get("source_path"),
        target_path=payload.get("target_path"),
    )


def _occurrence_from_payload(payload: dict[str, Any]) -> RelationshipOccurrence:
    return RelationshipOccurrence(
        path=str(payload.get("path") or ""),
        line_range=payload.get("line_range"),
        enclosing_symbol_id=payload.get("enclosing_symbol_id"),
        name=payload.get("name"),
        kind=payload.get("kind"),
        metadata=dict(payload.get("metadata") or {}),
        snippet=payload.get("snippet"),
    )


def _import_prelude_from_payload(payload: dict[str, Any]) -> ImportPrelude:
    return ImportPrelude(
        path=str(payload.get("path") or ""),
        language=str(payload.get("language") or "text"),
        text=str(payload.get("text") or ""),
        line_range=payload.get("line_range"),
        source_node_ids=list(payload.get("source_node_ids") or []),
        resolved_imports=list(payload.get("resolved_imports") or []),
    )


def _sufficiency_from_payload(payload: dict[str, Any]) -> SufficiencyResult:
    metrics_payload = payload.get("metrics") or {}
    metrics_fields = getattr(SufficiencyMetrics, "__dataclass_fields__", {})
    metrics_kwargs: dict[str, float] = {}
    for field in metrics_fields:
        if field in metrics_payload:
            value = metrics_payload.get(field)
            metrics_kwargs[field] = float(value) if value is not None else 0.0
    for field, field_def in metrics_fields.items():
        if field not in metrics_kwargs:
            default = getattr(field_def, "default", None)
            metrics_kwargs[field] = float(default) if default is not None else 0.0
    return SufficiencyResult(
        sufficient=bool(payload.get("sufficient")),
        metrics=SufficiencyMetrics(**metrics_kwargs),
        thresholds=dict(payload.get("thresholds") or {}),
        failure_reasons=list(payload.get("failure_reasons") or []),
        recovery=list(payload.get("recovery") or []),
    )


def _index_stats(payload: dict[str, Any], elapsed_ms: float) -> dict[str, Any]:
    return {
        "files": int(payload.get("files_indexed") or 0),
        "symbols": int(payload.get("symbols_indexed") or 0),
        "edges": int(payload.get("edges_indexed") or 0),
        "parse_errors": len(payload.get("parse_errors") or {}),
        "elapsed_ms": elapsed_ms,
        "phases": dict(payload.get("timings_ms") or {}),
        "transport": "mcp-stdio",
    }


def _server_command(args: argparse.Namespace) -> tuple[str, list[str]]:
    command = args.mcp_command or os.environ.get("CSEGRAPH_MCP_COMMAND") or sys.executable
    raw_args = args.mcp_args or os.environ.get("CSEGRAPH_MCP_ARGS") or "-m csegraph._cli serve"
    return command, shlex.split(raw_args)


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def _threshold_violations(payload: dict[str, Any], args: argparse.Namespace) -> list[str]:
    summary = payload["summary"]
    violations: list[str] = []

    if summary["overall_hit_rate"] < args.min_overall_hit_rate:
        violations.append(
            f"overall_hit_rate {summary['overall_hit_rate']} < {args.min_overall_hit_rate}"
        )
    if summary["task_pass_rate"] < args.min_task_pass_rate:
        violations.append(f"task_pass_rate {summary['task_pass_rate']} < {args.min_task_pass_rate}")
    if summary["failed_task_count"] > args.max_failed_tasks:
        violations.append(
            f"failed_task_count {summary['failed_task_count']} > {args.max_failed_tasks}"
        )
    if summary["avg_context_tokens"] > args.max_avg_context_tokens:
        violations.append(
            f"avg_context_tokens {summary['avg_context_tokens']} > {args.max_avg_context_tokens}"
        )
    if summary["avg_response_bytes"] > args.max_avg_response_bytes:
        violations.append(
            f"avg_response_bytes {summary['avg_response_bytes']} > {args.max_avg_response_bytes}"
        )
    sufficient_rate = (
        summary["sufficient_task_count"] / summary["task_count"] if summary["task_count"] else 1.0
    )
    if sufficient_rate < args.min_sufficient_task_rate:
        violations.append(
            f"sufficient_task_rate {round(sufficient_rate, 4)} < {args.min_sufficient_task_rate}"
        )

    for task in payload["tasks"]:
        if task["error"] is not None:
            violations.append(f"{task['task_id']} errored: {task['error']}")
        if task["returned_node_count"] > args.max_returned_node_count:
            violations.append(
                f"{task['task_id']} returned_node_count "
                f"{task['returned_node_count']} > {args.max_returned_node_count}"
            )
        if task["hit_rate"] < args.min_task_hit_rate:
            violations.append(
                f"{task['task_id']} hit_rate {task['hit_rate']} < {args.min_task_hit_rate}"
            )

    return violations


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run context benchmark corpus through MCP stdio and fail on thresholds.",
    )
    parser.add_argument("--repo", default=".", help="Repository root to benchmark.")
    parser.add_argument(
        "--corpus",
        default="benchmarks/context_quality/csegraph_self.json",
        help="Benchmark corpus JSON path.",
    )
    parser.add_argument("--db", default=None, help="Optional SQLite database output path.")
    parser.add_argument("--profile", default="small", help="CseGraph profile to benchmark.")
    parser.add_argument(
        "--postprocess-level", default="minimal", help="MCP index postprocess level."
    )
    parser.add_argument("--mcp-command", default=None, help="MCP server command.")
    parser.add_argument("--mcp-args", default=None, help="MCP server arguments.")
    parser.add_argument(
        "--min-overall-hit-rate",
        type=float,
        default=DEFAULT_MIN_OVERALL_HIT_RATE,
    )
    parser.add_argument(
        "--min-task-pass-rate",
        type=float,
        default=DEFAULT_MIN_TASK_PASS_RATE,
    )
    parser.add_argument("--min-task-hit-rate", type=float, default=DEFAULT_MIN_TASK_HIT_RATE)
    parser.add_argument(
        "--min-sufficient-task-rate",
        type=float,
        default=DEFAULT_MIN_SUFFICIENT_TASK_RATE,
    )
    parser.add_argument("--max-failed-tasks", type=int, default=DEFAULT_MAX_FAILED_TASKS)
    parser.add_argument(
        "--max-avg-context-tokens",
        type=int,
        default=DEFAULT_MAX_AVG_CONTEXT_TOKENS,
    )
    parser.add_argument(
        "--max-avg-response-bytes",
        type=int,
        default=DEFAULT_MAX_AVG_RESPONSE_BYTES,
    )
    parser.add_argument(
        "--max-returned-node-count",
        type=int,
        default=DEFAULT_MAX_RETURNED_NODE_COUNT,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
