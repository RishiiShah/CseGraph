"""Compare adaptive retrieval with a versioned rg/Pyright selective-read baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import csegraph
from csegraph import ContextRequest, ContextService, IndexService, to_dict
from csegraph._core.retrieval.token_budget import (
    DEFAULT_ENCODING,
    count_payload_tokens,
    response_tokens,
    token_measurement,
)
from tools.adaptive_benchmark import (
    LOCAL_COPY_URLS,
    PINNED_PYRIGHT_VERSION,
    AdaptiveBenchmarkCorpus,
    AdaptiveBenchmarkTask,
    BenchmarkRepository,
    PyrightLspProvider,
    StrongBaselineAdapter,
    benchmark_workspace_hygiene,
    build_adaptive_corpus,
    copy_benchmark_repository,
    corpus_completeness,
    corpus_quality,
    corpus_to_payload,
    execute_benchmark_task,
    load_adaptive_corpus,
    prepare_benchmark_repository,
)

REPORT_SCHEMA_VERSION = "csegraph-adaptive-retrieval-report-v4"
RUNNER_VERSION = "2.3"
DIAGNOSTIC_BUDGET_SLACK = 1024


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--corpus",
        default="pr",
        help="Named source-driven corpus (pr, nightly, release) or a JSON path",
    )
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--bootstrap-missing", action="store_true")
    parser.add_argument("--budget", type=int, default=800)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--modes",
        default="cold,warm",
        help="Comma-separated measurements: cold,warm (warm includes an unreported warmup)",
    )
    parser.add_argument("--warm-runs", type=int, default=1)
    parser.add_argument(
        "--samples",
        type=int,
        default=None,
        help="Alias for --warm-runs; use with warm mode for repeated measurement samples",
    )
    parser.add_argument(
        "--pyright",
        choices=("auto", "off", "required"),
        default="auto",
        help=f"Use pinned Pyright {PINNED_PYRIGHT_VERSION} when available",
    )
    parser.add_argument("--execute-tasks", action="store_true")
    parser.add_argument(
        "--agent-command",
        default=None,
        help="Agent argv with optional {task}, {target}, {repo}, and {task_id} placeholders",
    )
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--output", default=None)
    parser.add_argument("--fail-on-gates", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    corpus_path: Path | None = None
    if args.corpus in {"pr", "nightly", "release"}:
        corpus = build_adaptive_corpus(args.corpus, repo_root=repo_root)
        corpus_digest = hashlib.sha256(
            json.dumps(
                corpus_to_payload(corpus),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    else:
        corpus_path = Path(args.corpus).resolve()
        corpus = load_adaptive_corpus(corpus_path)
        corpus_digest = _sha256(corpus_path)
    tasks = list(corpus.tasks)
    if args.limit is not None:
        tasks = tasks[: max(0, args.limit)]
    modes = _parse_modes(args.modes)
    if args.samples is not None:
        args.warm_runs = args.samples
    if args.warm_runs < 1:
        parser.error("--warm-runs/--samples must be at least 1")

    cache_root = (
        Path(args.cache_dir).resolve()
        if args.cache_dir
        else Path(tempfile.gettempdir()) / "csegraph-benchmark-repositories"
    )
    provider = None if args.pyright == "off" else PyrightLspProvider()
    baseline = StrongBaselineAdapter(definition_provider=provider)
    started_at = datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []
    repository_results: dict[str, dict[str, Any]] = {}

    with tempfile.TemporaryDirectory(prefix="csegraph-adaptive-benchmark-") as tmp:
        scratch = Path(tmp)
        indexed: dict[str, tuple[Path, Path, float]] = {}
        for task in tasks:
            repository = corpus.repositories.get(task.repo)
            if repository is None:
                results.append(_unmeasured_task(task, "repository_not_declared"))
                continue
            if task.repo not in indexed and task.repo not in repository_results:
                prepared = prepare_benchmark_repository(
                    repository,
                    repo_root=repo_root,
                    cache_root=cache_root,
                    bootstrap_missing=args.bootstrap_missing,
                )
                repository_results[task.repo] = {
                    "requested_path": str((repo_root / task.repo).resolve()),
                    "resolved_path": str(prepared.path) if prepared.path else None,
                    "url": repository.url,
                    "expected_commit": repository.commit,
                    "observed_commit": prepared.observed_commit,
                    "commit_matches": prepared.commit_matches,
                    "bootstrapped": prepared.bootstrapped,
                    "reason": prepared.reason,
                }
                if prepared.path is not None and prepared.commit_matches:
                    benchmark_repo = prepared.path
                    if _copy_repository_for_benchmark(repository):
                        benchmark_repo = scratch / f"fixture-{len(indexed)}"
                        hygiene = copy_benchmark_repository(prepared.path, benchmark_repo)
                    else:
                        hygiene = benchmark_workspace_hygiene(benchmark_repo)
                    db = scratch / f"repo-{len(indexed)}.db"
                    index_started = time.perf_counter()
                    index_result = IndexService(db).index(benchmark_repo)
                    index_ms = (time.perf_counter() - index_started) * 1000
                    repository_results[task.repo]["workspace_hygiene"] = hygiene
                    repository_results[task.repo]["index"] = _index_report(
                        index_result,
                        db,
                        index_ms,
                    )
                    indexed[task.repo] = (benchmark_repo, db, index_ms)

            prepared_index = indexed.get(task.repo)
            if prepared_index is None:
                reason = str(repository_results[task.repo].get("reason") or "not_indexed")
                results.append(_unmeasured_task(task, reason))
                continue
            repo, db, index_ms = prepared_index
            results.append(
                _run_retrieval_task(
                    task,
                    repo,
                    db,
                    baseline,
                    budget=args.budget,
                    modes=modes,
                    warm_runs=args.warm_runs,
                    index_ms=index_ms,
                    execute_tasks=args.execute_tasks,
                    agent_command=(
                        tuple(shlex.split(args.agent_command)) if args.agent_command else None
                    ),
                    allow_network=args.allow_network,
                )
            )

    if provider is not None:
        provider.close()
    completeness = corpus_completeness(corpus)
    quality = corpus_quality(corpus)
    # A limited developer run is intentionally not a complete release-gate run.
    evaluated_complete_corpus = args.limit is None
    summary = _summary(
        results,
        args.budget,
        completeness=completeness,
        quality=quality,
        evaluated_complete_corpus=evaluated_complete_corpus,
        pyright_required=args.pyright == "required",
        pyright_available=bool(provider and provider.available),
    )
    finished_at = datetime.now(timezone.utc)
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
        "generated_at": finished_at.isoformat(),
        "duration_ms": round((finished_at - started_at).total_seconds() * 1000, 3),
        "corpus": {
            "path": str(corpus.path),
            "schema_version": corpus.schema_version,
            "version": corpus.version,
            "tier": corpus.tier,
            "status": corpus.status,
            "unsupported_reason": corpus.unsupported_reason,
            "sha256": corpus_digest,
            "completeness": completeness,
            "quality": quality,
            "limited_to": args.limit,
        },
        "configuration": {
            "token_budget": args.budget,
            "modes": list(modes),
            "warm_runs": args.warm_runs,
            "samples": args.warm_runs,
            "bootstrap_missing": args.bootstrap_missing,
            "pyright_mode": args.pyright,
            "execute_tasks": args.execute_tasks,
            "allow_network": args.allow_network,
        },
        "provenance": _provenance(
            repo_root,
            corpus,
            provider,
        ),
        "repositories": repository_results,
        "summary": summary,
        "tasks": results,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    if args.fail_on_gates and not summary["release_gates_passed"]:
        return 1
    return 0


def _run_retrieval_task(
    task: AdaptiveBenchmarkTask,
    repo: Path,
    db: Path,
    baseline: StrongBaselineAdapter,
    *,
    budget: int,
    modes: Sequence[str],
    warm_runs: int,
    index_ms: float,
    execute_tasks: bool,
    agent_command: Sequence[str] | None,
    allow_network: bool,
) -> dict[str, Any]:
    service = ContextService(db)
    measurements: dict[str, Any] = {}
    if "warm" in modes and "cold" not in modes:
        _measure_pair(task, repo, service, baseline, budget=budget, temperature="warmup")
    for mode in modes:
        repetitions = warm_runs if mode == "warm" else 1
        samples = [
            _measure_pair(task, repo, service, baseline, budget=budget, temperature=mode)
            for _ in range(repetitions)
        ]
        measurements[mode] = {
            "samples": samples,
            "baseline": _aggregate_system(samples, "baseline"),
            "adaptive": _aggregate_system(samples, "adaptive"),
        }
    selected_mode = "warm" if "warm" in measurements else modes[-1]
    baseline_result = measurements[selected_mode]["baseline"]
    adaptive_result = measurements[selected_mode]["adaptive"]
    expected = set(task.expected_locations)
    permitted = set(task.permitted_files)
    uses_v2_expectations = bool(
        task.expected_target
        or task.expected_candidates
        or task.required_evidence
        or task.permitted_ranges
        or task.expected_next_tool
        or task.expected_status != "ready"
    )
    if uses_v2_expectations:
        baseline_evaluation = _evaluate_v2_result(task, baseline_result, adaptive=False)
        adaptive_evaluation = _evaluate_v2_result(task, adaptive_result, adaptive=True)
    else:
        baseline_evaluation = {
            "recall": _recall(set(baseline_result["paths"]), expected),
            "target_found": (
                not task.expected_locations
                or task.expected_locations[0] in set(baseline_result["paths"])
            ),
            "precision": _precision(set(baseline_result["paths"]), permitted),
            "status_matched": baseline_result["status"] == "ready",
            "role_recall": 1.0,
            "next_tool_matched": True,
        }
        adaptive_evaluation = {
            "recall": _recall(set(adaptive_result["paths"]), expected),
            "target_found": adaptive_result["status"] == "ready",
            "precision": _precision(set(adaptive_result["paths"]), permitted),
            "status_matched": adaptive_result["status"] == "ready",
            "role_recall": 1.0,
            "next_tool_matched": True,
        }
    execution = None
    if execute_tasks:
        execution = asdict(
            execute_benchmark_task(
                task,
                repo,
                agent_command=agent_command,
                allow_network=allow_network,
            )
        )
    return {
        "id": task.id,
        "status": "measured",
        "category": task.category,
        "execution_mode": task.execution_mode,
        "repo": task.repo,
        "commit": task.commit,
        "index_ms": round(index_ms, 3),
        "expected_locations": sorted(expected),
        "permitted_files": sorted(permitted),
        "expected_status": task.expected_status,
        **(
            {"expected_target": asdict(task.expected_target)}
            if task.expected_target is not None
            else {}
        ),
        **(
            {"expected_candidates": [asdict(item) for item in task.expected_candidates]}
            if task.expected_candidates
            else {}
        ),
        **(
            {"required_evidence": [asdict(item) for item in task.required_evidence]}
            if task.required_evidence
            else {}
        ),
        **(
            {"permitted_ranges": [asdict(item) for item in task.permitted_ranges]}
            if task.permitted_ranges
            else {}
        ),
        "selected_mode": selected_mode,
        "measurements": measurements,
        "baseline": {
            **baseline_result,
            **baseline_evaluation,
        },
        "adaptive": {
            **adaptive_result,
            **adaptive_evaluation,
        },
        **({"execution": execution} if execution is not None else {}),
    }


def _measure_pair(
    task: AdaptiveBenchmarkTask,
    repo: Path,
    service: ContextService,
    baseline: StrongBaselineAdapter,
    *,
    budget: int,
    temperature: str,
) -> dict[str, Any]:
    baseline_started = time.perf_counter()
    baseline_result = baseline.retrieve(
        repo,
        task.task,
        target=task.target,
        task_kind=task.category,
        token_budget=budget,
        temperature=temperature,
    )
    baseline_observed_ms = (time.perf_counter() - baseline_started) * 1000

    adaptive_started = time.perf_counter()
    adaptive = service.retrieve(
        ContextRequest(
            repo=str(repo),
            task=task.task,
            target=task.target,
            task_kind=_context_task_kind(task.category),
            token_budget=_diagnostic_measurement_budget(task, budget),
            diagnostic=True,
        )
    )
    adaptive_observed_ms = (time.perf_counter() - adaptive_started) * 1000
    adaptive_payload = to_dict(adaptive)
    adaptive_diagnostics = adaptive_payload.get("diagnostics") or {}
    adaptive_usage = adaptive_diagnostics.get("usage") or {}
    adaptive_engine_ms = float(adaptive_usage.get("latency_ms") or adaptive_observed_ms)
    adaptive_diagnostic_tokens = int(adaptive_usage.get("tokens") or response_tokens(adaptive))
    adaptive_tokens = _content_tokens_without_diagnostics(adaptive_payload)
    target_slice = next((item for item in adaptive.slices if item.role == "target"), None)
    adaptive_target = (
        {
            "id": target_slice.id,
            "name": target_slice.symbol,
            "path": target_slice.path,
            "lines": target_slice.lines,
        }
        if target_slice is not None
        else None
    )
    return {
        "baseline": {
            "status": "ready" if baseline_result.slices else "empty",
            "tokens": baseline_result.usage["tokens"],
            "measurement": baseline_result.usage.get("measurement"),
            "tool_observed_latency_ms": round(baseline_observed_ms, 3),
            "tool_latency_ms": baseline_result.usage["tool_latency_ms"],
            "engine_latency_ms": baseline_result.usage["engine_latency_ms"],
            "external_tool_latency_ms": baseline_result.usage["external_tool_latency_ms"],
            "rg_latency_ms": baseline_result.usage["rg_latency_ms"],
            "lsp_latency_ms": baseline_result.usage["lsp_latency_ms"],
            "tool_calls": baseline_result.usage["tool_calls"],
            "paths": sorted({item.path for item in baseline_result.slices}),
            "slices": [asdict(item) for item in baseline_result.slices],
            "warnings": baseline_result.warnings,
        },
        "adaptive": {
            "status": adaptive_payload["status"],
            "tokens": adaptive_tokens,
            "diagnostic_tokens": adaptive_diagnostic_tokens,
            "diagnostic_budget": _diagnostic_measurement_budget(task, budget),
            "measurement": adaptive_usage.get("measurement") or token_measurement("o200k_base"),
            "tool_observed_latency_ms": round(adaptive_observed_ms, 3),
            "tool_latency_ms": round(adaptive_observed_ms, 3),
            "engine_latency_ms": round(adaptive_engine_ms, 3),
            "external_tool_latency_ms": round(
                max(0.0, adaptive_observed_ms - adaptive_engine_ms), 3
            ),
            "tool_calls": 1,
            "cache": adaptive_usage.get("cache", "disabled"),
            "paths": sorted({item.path for item in adaptive.slices}),
            "target": adaptive_target,
            "candidates": adaptive_payload.get("candidates", []),
            "slices": adaptive_payload.get("slices", []),
            "next": adaptive_payload.get("next"),
        },
    }


def _context_task_kind(category: str) -> str:
    return {
        "definition": "understand",
        "debug": "test-impact",
        "refactor": "edit",
        "cross-file": "edit",
        "test-impact": "test-impact",
    }.get(category, "auto")


def _diagnostic_measurement_budget(task: AdaptiveBenchmarkTask, content_budget: int) -> int:
    if task.expected_status == "insufficient":
        return content_budget
    return min(16_384, content_budget + DIAGNOSTIC_BUDGET_SLACK)


def _content_tokens_without_diagnostics(payload: dict[str, Any]) -> int:
    content_payload = dict(payload)
    content_payload.pop("diagnostics", None)
    return count_payload_tokens(content_payload, DEFAULT_ENCODING)


def _aggregate_system(samples: Sequence[dict[str, Any]], system: str) -> dict[str, Any]:
    values = [sample[system] for sample in samples]
    representative = dict(values[-1])
    for key in (
        "tokens",
        "tool_observed_latency_ms",
        "tool_latency_ms",
        "engine_latency_ms",
        "external_tool_latency_ms",
        "tool_calls",
    ):
        numeric = [float(value[key]) for value in values if key in value]
        if numeric:
            median = statistics.median(numeric)
            representative[key] = (
                int(median) if key in {"tokens", "tool_calls"} else round(median, 3)
            )
    representative["sample_count"] = len(values)
    return representative


def _unmeasured_task(task: AdaptiveBenchmarkTask, reason: str) -> dict[str, Any]:
    return {
        "id": task.id,
        "status": "unmeasured",
        "category": task.category,
        "repo": task.repo,
        "commit": task.commit,
        "reason": reason,
        "expected_locations": list(task.expected_locations),
        "permitted_files": list(task.permitted_files),
    }


def _evaluate_v2_result(
    task: AdaptiveBenchmarkTask,
    result: dict[str, Any],
    *,
    adaptive: bool,
) -> dict[str, Any]:
    slices = [item for item in result.get("slices", []) if isinstance(item, dict)]
    status_matched = result.get("status") == task.expected_status

    if task.expected_status == "ambiguous":
        candidates = (
            [item for item in result.get("candidates", []) if isinstance(item, dict)]
            if adaptive
            else slices
        )
        target_found = all(
            any(_item_matches_target(item, expected) for item in candidates)
            for expected in task.expected_candidates
        )
    elif task.expected_target is not None:
        target_item = result.get("target") if adaptive else (slices[0] if slices else None)
        target_found = isinstance(target_item, dict) and _item_matches_target(
            target_item,
            task.expected_target,
        )
    else:
        target_found = status_matched

    evidence_hits = 0
    role_hits = 0
    for expected in task.required_evidence:
        matching = [
            item for item in slices if _item_contains_line(item, expected.path, expected.line)
        ]
        if matching:
            evidence_hits += 1
        if matching and (
            not adaptive
            or expected.role is None
            or any(item.get("role") == expected.role for item in matching)
        ):
            role_hits += 1
    recall = evidence_hits / len(task.required_evidence) if task.required_evidence else 1.0
    role_recall = role_hits / len(task.required_evidence) if task.required_evidence else 1.0

    if not slices:
        precision = 1.0 if task.expected_status != "ready" else 0.0
    else:
        precise = sum(
            any(_slice_overlaps_range(item, permitted) for permitted in task.permitted_ranges)
            for item in slices
        )
        precision = precise / len(slices)

    expected_next = task.expected_next_tool
    next_value = result.get("next")
    next_tool_matched = expected_next is None or (
        isinstance(next_value, dict) and next_value.get("tool") == expected_next
    )
    return {
        "recall": recall,
        "target_found": target_found,
        "precision": precision,
        "status_matched": status_matched,
        "role_recall": role_recall,
        "next_tool_matched": next_tool_matched,
    }


def _item_matches_target(item: dict[str, Any], expected: Any) -> bool:
    if str(item.get("path") or "") != expected.path:
        return False
    if not _item_contains_line(item, expected.path, expected.line):
        return False
    if expected.id is not None and item.get("id") is not None:
        if str(item["id"]) != expected.id:
            return False
    if expected.name is not None and item.get("name") is not None:
        if str(item["name"]) != expected.name:
            return False
    return True


def _item_contains_line(item: dict[str, Any], path: str, line: int) -> bool:
    if str(item.get("path") or "") != path:
        return False
    lines = item.get("lines")
    return isinstance(lines, list) and len(lines) == 2 and int(lines[0]) <= line <= int(lines[1])


def _slice_overlaps_range(item: dict[str, Any], permitted: Any) -> bool:
    if str(item.get("path") or "") != permitted.path:
        return False
    lines = item.get("lines")
    return (
        isinstance(lines, list)
        and len(lines) == 2
        and int(lines[0]) <= permitted.end_line
        and int(lines[1]) >= permitted.start_line
    )


def _recall(paths: set[str], expected: set[str]) -> float:
    if not expected:
        return 1.0
    return len(paths & expected) / len(expected)


def _precision(paths: set[str], permitted: set[str]) -> float:
    if not paths:
        return 0.0
    if not permitted:
        return 1.0
    return len(paths & permitted) / len(paths)


def _summary(
    results: list[dict[str, Any]],
    token_budget: int,
    *,
    completeness: dict[str, Any],
    quality: dict[str, Any],
    evaluated_complete_corpus: bool,
    pyright_required: bool,
    pyright_available: bool,
) -> dict[str, Any]:
    measured = [item for item in results if item["status"] == "measured"]
    if not measured:
        gates = {
            "corpus_complete": bool(completeness["complete"]),
            "complete_corpus_evaluated": evaluated_complete_corpus,
            "all_tasks_measured": False,
            "pyright_requirement_met": not pyright_required or pyright_available,
            "corpus_quality_passed": bool(quality["passed"]),
        }
        return {
            "task_count": len(results),
            "measured_task_count": 0,
            "quality_gates": quality["gates"],
            "gates": gates,
            "release_gates_passed": False,
        }
    adaptive = [item["adaptive"] for item in measured]
    baseline = [item["baseline"] for item in measured]
    adaptive_tokens = [int(item["tokens"]) for item in adaptive]
    baseline_tokens = [int(item["tokens"]) for item in baseline]
    adaptive_tool_latency = [float(item["tool_observed_latency_ms"]) for item in adaptive]
    baseline_tool_latency = [float(item["tool_observed_latency_ms"]) for item in baseline]
    adaptive_engine_latency = [float(item["engine_latency_ms"]) for item in adaptive]
    baseline_engine_latency = [float(item["engine_latency_ms"]) for item in baseline]
    target_rate = statistics.mean(1.0 if item["target_found"] else 0.0 for item in adaptive)
    status_rate = statistics.mean(1.0 if item["status_matched"] else 0.0 for item in adaptive)
    recall = statistics.mean(float(item["recall"]) for item in adaptive)
    precision = statistics.mean(float(item["precision"]) for item in adaptive)
    role_recall = statistics.mean(float(item["role_recall"]) for item in adaptive)
    next_tool_rate = statistics.mean(1.0 if item["next_tool_matched"] else 0.0 for item in adaptive)
    baseline_recall = statistics.mean(float(item["recall"]) for item in baseline)
    baseline_target_rate = statistics.mean(
        1.0 if item["target_found"] else 0.0 for item in baseline
    )
    within_budget = all(tokens <= token_budget for tokens in adaptive_tokens)
    token_ratio = statistics.median(adaptive_tokens) / max(1.0, statistics.median(baseline_tokens))
    tool_latency_delta = _p95(adaptive_tool_latency) - _p95(baseline_tool_latency)
    engine_latency_delta = _p95(adaptive_engine_latency) - _p95(baseline_engine_latency)
    gates = {
        "corpus_complete": bool(completeness["complete"]),
        "complete_corpus_evaluated": evaluated_complete_corpus,
        "all_tasks_measured": len(measured) == len(results),
        "pyright_requirement_met": not pyright_required or pyright_available,
        "corpus_quality_passed": bool(quality["passed"]),
        "baseline_nonempty": all(item["status"] == "ready" for item in baseline),
        "within_budget": within_budget,
        "target_ready_rate_100pct": target_rate == 1.0,
        "expected_status_rate_100pct": status_rate == 1.0,
        "slice_recall_100pct": recall == 1.0,
        "slice_precision_at_least_95pct": precision >= 0.95,
        "median_token_ratio_at_most_35pct": token_ratio <= 0.35,
        "engine_p95_overhead_below_100ms": engine_latency_delta < 100.0,
    }
    execution_results = [
        item["execution"]
        for item in measured
        if item.get("execution") and item["execution"]["status"] != "retrieval_only"
    ]
    if execution_results:
        gates["agent_tasks_passed"] = all(
            result["status"] == "passed" for result in execution_results
        )
    return {
        "task_count": len(results),
        "measured_task_count": len(measured),
        "target_ready_rate": round(target_rate, 4),
        "expected_status_rate": round(status_rate, 4),
        "adaptive_recall": round(recall, 4),
        "adaptive_precision": round(precision, 4),
        "adaptive_role_recall": round(role_recall, 4),
        "adaptive_next_tool_rate": round(next_tool_rate, 4),
        "baseline_recall": round(baseline_recall, 4),
        "baseline_target_rate": round(baseline_target_rate, 4),
        "adaptive_median_tokens": statistics.median(adaptive_tokens),
        "baseline_median_tokens": statistics.median(baseline_tokens),
        "adaptive_to_baseline_token_ratio": round(token_ratio, 4),
        "adaptive_tool_p95_latency_ms": round(_p95(adaptive_tool_latency), 3),
        "baseline_tool_p95_latency_ms": round(_p95(baseline_tool_latency), 3),
        "tool_p95_latency_delta_ms": round(tool_latency_delta, 3),
        "adaptive_engine_p95_latency_ms": round(_p95(adaptive_engine_latency), 3),
        "baseline_engine_p95_latency_ms": round(_p95(baseline_engine_latency), 3),
        "engine_p95_latency_delta_ms": round(engine_latency_delta, 3),
        "by_repo": _by_repo_summary(measured),
        "quality_gates": quality["gates"],
        "gates": gates,
        "release_gates_passed": all(gates.values()),
    }


def _by_repo_summary(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        grouped.setdefault(str(result["repo"]), []).append(result)
    return {repo: _result_group_summary(items) for repo, items in sorted(grouped.items())}


def _result_group_summary(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    adaptive = [item["adaptive"] for item in results]
    baseline = [item["baseline"] for item in results]
    adaptive_tokens = [int(item["tokens"]) for item in adaptive]
    baseline_tokens = [int(item["tokens"]) for item in baseline]
    adaptive_task_latencies = [float(item["tool_observed_latency_ms"]) for item in adaptive]
    baseline_task_latencies = [float(item["tool_observed_latency_ms"]) for item in baseline]
    adaptive_samples = _selected_system_samples(results, "adaptive")
    baseline_samples = _selected_system_samples(results, "baseline")
    adaptive_sample_latencies = [
        float(item["tool_observed_latency_ms"]) for item in adaptive_samples
    ]
    baseline_sample_latencies = [
        float(item["tool_observed_latency_ms"]) for item in baseline_samples
    ]
    status_counts: dict[str, int] = {}
    for item in adaptive:
        status = str(item.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "task_count": len(results),
        "adaptive_sample_count": len(adaptive_samples),
        "baseline_sample_count": len(baseline_samples),
        "adaptive_status_counts": dict(sorted(status_counts.items())),
        "expected_status_rate": round(
            statistics.mean(1.0 if item["status_matched"] else 0.0 for item in adaptive),
            4,
        ),
        "adaptive_recall": round(statistics.mean(float(item["recall"]) for item in adaptive), 4),
        "adaptive_precision": round(
            statistics.mean(float(item["precision"]) for item in adaptive),
            4,
        ),
        "adaptive_role_recall": round(
            statistics.mean(float(item["role_recall"]) for item in adaptive),
            4,
        ),
        "adaptive_median_tokens": statistics.median(adaptive_tokens),
        "baseline_median_tokens": statistics.median(baseline_tokens),
        "adaptive_to_baseline_token_ratio": round(
            statistics.median(adaptive_tokens) / max(1.0, statistics.median(baseline_tokens)),
            4,
        ),
        "adaptive_mean_tool_latency_ms": _mean(adaptive_sample_latencies),
        "adaptive_tool_latency_stdev_ms": _stdev(adaptive_sample_latencies),
        "adaptive_tool_p95_latency_ms": round(_p95(adaptive_task_latencies), 3),
        "baseline_mean_tool_latency_ms": _mean(baseline_sample_latencies),
        "baseline_tool_latency_stdev_ms": _stdev(baseline_sample_latencies),
        "baseline_tool_p95_latency_ms": round(_p95(baseline_task_latencies), 3),
    }


def _selected_system_samples(
    results: Sequence[dict[str, Any]],
    system: str,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for result in results:
        mode = str(result["selected_mode"])
        mode_measurements = result.get("measurements", {}).get(mode, {})
        for sample in mode_measurements.get("samples", []):
            value = sample.get(system) if isinstance(sample, dict) else None
            if isinstance(value, dict):
                samples.append(value)
    return samples


def _copy_repository_for_benchmark(repository: BenchmarkRepository) -> bool:
    return repository.url in LOCAL_COPY_URLS


def _index_report(index_result: Any, db_path: Path, index_ms: float) -> dict[str, Any]:
    timings = dict(getattr(index_result, "timings_ms", {}) or {})
    parse_cache_path = db_path.with_name("parse_cache.db")
    return {
        "files_indexed": int(getattr(index_result, "files_indexed", 0)),
        "symbols_indexed": int(getattr(index_result, "symbols_indexed", 0)),
        "edges_indexed": int(getattr(index_result, "edges_indexed", 0)),
        "cache_hits": int(getattr(index_result, "cache_hits", 0)),
        "cache_misses": int(getattr(index_result, "cache_misses", 0)),
        "index_ms": round(index_ms, 3),
        "discover_parse_ms": round(float(timings.get("discover_parse") or 0.0), 3),
        "write_graph_ms": round(float(timings.get("write_graph") or 0.0), 3),
        "db_size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "parse_cache_size_bytes": (
            parse_cache_path.stat().st_size if parse_cache_path.exists() else 0
        ),
        "timings_ms": timings,
    }


def _parse_modes(raw: str) -> tuple[str, ...]:
    modes = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    if not modes or any(mode not in {"cold", "warm"} for mode in modes):
        raise SystemExit("--modes must contain cold, warm, or cold,warm")
    return modes


def _provenance(
    repo_root: Path,
    corpus: AdaptiveBenchmarkCorpus,
    provider: PyrightLspProvider | None,
) -> dict[str, Any]:
    return {
        "csegraph_version": csegraph.__version__,
        "runner_git_commit": _command_output(["git", "-C", str(repo_root), "rev-parse", "HEAD"]),
        "runner_git_dirty": bool(
            _command_output(["git", "-C", str(repo_root), "status", "--porcelain"])
        ),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "rg_version": _first_line(_command_output(["rg", "--version"])),
        "git_version": _first_line(_command_output(["git", "--version"])),
        "pyright": {
            "enabled": provider is not None,
            "available": bool(provider and provider.available),
            "expected_version": PINNED_PYRIGHT_VERSION,
            "observed_version": provider.observed_version if provider else None,
            "warning": provider.warning if provider else "disabled by configuration",
        },
        "corpus_version": corpus.version,
        "command": sys.argv,
    }


def _command_output(argv: Sequence[str]) -> str | None:
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _first_line(value: str | None) -> str | None:
    return value.splitlines()[0] if value else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _p95(values: list[float]) -> float:
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(0.95 * (len(ordered) - 1))))
    return ordered[index]


def _mean(values: Sequence[float]) -> float:
    return round(statistics.fmean(values), 3) if values else 0.0


def _stdev(values: Sequence[float]) -> float:
    return round(statistics.pstdev(values), 3) if len(values) > 1 else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
