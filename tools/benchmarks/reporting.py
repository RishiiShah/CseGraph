"""Evaluation and reporting helpers for adaptive benchmark runs."""

from __future__ import annotations

import statistics
from typing import Any, Sequence

from csegraph._core.retrieval.token_budget import (
    DEFAULT_ENCODING,
    count_payload_tokens,
)
from tools.benchmarks.models import AdaptiveBenchmarkTask

DIAGNOSTIC_BUDGET_SLACK = 1024


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
