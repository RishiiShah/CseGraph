"""Quality and completeness checks for adaptive benchmark corpora."""

from __future__ import annotations

from typing import Any, Iterable

from tools.benchmarks.models import AdaptiveBenchmarkCorpus
from tools.benchmarks.schema import TASK_SCHEMA_VERSION_V2


def corpus_quality(corpus: AdaptiveBenchmarkCorpus) -> dict[str, Any]:
    """Return benchmark task-mix quality metrics, warnings, and enforceable gates."""

    tasks = list(corpus.tasks)
    task_count = len(tasks)
    category_counts = _counts(task.category for task in tasks)
    status_counts = _counts(task.expected_status for task in tasks)
    execution_mode_counts = _counts(task.execution_mode for task in tasks)
    explicit_target_count = sum(task.target is not None for task in tasks)
    targetless_count = task_count - explicit_target_count
    ambiguous_count = sum(
        task.category == "ambiguous" or task.expected_status == "ambiguous" for task in tasks
    )
    structural_followup_count = sum(
        task.category == "structural" and task.expected_next_tool is not None for task in tasks
    )
    agent_task_count = sum(task.execution_mode == "agent" for task in tasks)
    required_test_evidence_count = sum(
        any(evidence.path.startswith(("test/", "tests/")) for evidence in task.required_evidence)
        for task in tasks
    )
    insufficient_budget_count = sum(task.expected_status == "insufficient" for task in tasks)
    exact_target_ratio = explicit_target_count / task_count if task_count else 0.0

    gates = {
        "targetless_coverage": targetless_count > 0,
        "ambiguous_coverage": ambiguous_count > 0,
        "structural_followup_coverage": structural_followup_count > 0,
        "agent_task_coverage": agent_task_count > 0,
        "required_test_evidence": required_test_evidence_count > 0,
        "insufficient_budget_coverage": insufficient_budget_count > 0,
        "exact_target_ratio_at_most_90pct": exact_target_ratio <= 0.90,
    }
    warnings: list[str] = []
    if task_count and explicit_target_count == task_count:
        warnings.append("all_tasks_have_explicit_targets")
    elif exact_target_ratio > 0.85:
        warnings.append("explicit_target_ratio_high")
    if not gates["targetless_coverage"]:
        warnings.append("targetless_coverage_missing")
    if not gates["ambiguous_coverage"]:
        warnings.append("ambiguous_coverage_missing")
    if not gates["structural_followup_coverage"]:
        warnings.append("structural_followup_coverage_missing")
    if not gates["agent_task_coverage"]:
        warnings.append("agent_task_coverage_missing")
    if not gates["required_test_evidence"]:
        warnings.append("required_test_evidence_missing")
    if not gates["insufficient_budget_coverage"]:
        warnings.append("insufficient_budget_coverage_missing")

    # Agent execution coverage is contract-enforced for PR corpora, while the
    # existing larger corpora remain retrieval-quality focused. Task execution
    # itself remains opt-in.
    # The perf and broad corpora intentionally favor stable exact targets for
    # high-N latency averages while keeping ambiguity/insufficient/structural
    # coverage from the sandbox release seed; exact-target ratio is therefore a
    # warning, not a perf/broad-tier failure.
    enforced_gate_names = tuple(
        name
        for name in gates
        if not (
            (name == "agent_task_coverage" and corpus.tier != "pr")
            or (corpus.tier in {"perf", "broad"} and name == "exact_target_ratio_at_most_90pct")
            or (
                corpus.tier == "sandbox"
                and name
                in {
                    "ambiguous_coverage",
                    "required_test_evidence",
                    "insufficient_budget_coverage",
                }
            )
        )
    )
    enforced = corpus.tier in {"pr", "release", "perf", "broad", "sandbox"}
    passed = not enforced or all(gates[name] for name in enforced_gate_names)
    return {
        "metrics": {
            "task_count": task_count,
            "category_counts": category_counts,
            "status_counts": status_counts,
            "execution_mode_counts": execution_mode_counts,
            "explicit_target_count": explicit_target_count,
            "targetless_count": targetless_count,
            "explicit_target_ratio": round(exact_target_ratio, 4),
            "ambiguous_count": ambiguous_count,
            "structural_followup_count": structural_followup_count,
            "agent_task_count": agent_task_count,
            "required_test_evidence_count": required_test_evidence_count,
            "insufficient_budget_count": insufficient_budget_count,
        },
        "warnings": warnings,
        "gates": gates,
        "enforced": enforced,
        "enforced_gate_names": enforced_gate_names,
        "passed": passed,
    }


def corpus_completeness(corpus: AdaptiveBenchmarkCorpus) -> dict[str, Any]:
    expected_counts = {
        "pr": 22,
        "nightly": 60,
        "release": 30,
        "perf": 220,
        "broad": 348,
        "sandbox": 364,
    }
    expected = expected_counts[corpus.tier]
    supported = [task for task in corpus.tasks if task.supported]
    invalid_tasks: list[str] = []
    for task in supported:
        if corpus.schema_version == TASK_SCHEMA_VERSION_V2:
            expected_result_present = (
                task.expected_target is not None
                if task.expected_status == "ready"
                else bool(task.expected_candidates)
                if task.expected_status == "ambiguous"
                else True
            )
            if (
                not expected_result_present
                or not task.permitted_ranges
                or (task.expected_status == "ready" and not task.required_evidence)
            ):
                invalid_tasks.append(task.id)
                continue
        elif not task.expected_locations or not task.permitted_files:
            invalid_tasks.append(task.id)
            continue
        if task.execution_mode == "agent" and (not task.test_command or not task.hidden_checks):
            invalid_tasks.append(task.id)
    repository_pins_complete = all(
        task.repo in corpus.repositories and corpus.repositories[task.repo].commit == task.commit
        for task in supported
    )
    gates = {
        "corpus_status_ready": corpus.status == "ready",
        "task_count_exact": len(corpus.tasks) == expected,
        "all_tasks_supported": len(supported) == len(corpus.tasks),
        "task_contracts_complete": not invalid_tasks,
        "repository_pins_complete": repository_pins_complete,
    }
    return {
        "tier": corpus.tier,
        "expected_task_count": expected,
        "task_count": len(corpus.tasks),
        "supported_task_count": len(supported),
        "invalid_task_ids": invalid_tasks,
        "gates": gates,
        "complete": all(gates.values()),
    }


def _counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


__all__ = ["corpus_completeness", "corpus_quality"]
