"""Compare adaptive CseGraph retrieval with the strong rg/selective-read baseline."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from csegraph import ContextRequest, ContextService, IndexService, to_dict
from csegraph._core.benchmark_baseline import (
    StrongBaselineAdapter,
    load_adaptive_tasks,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--corpus",
        default=str(REPO_ROOT / "benchmarks" / "adaptive" / "pr_tasks.json"),
    )
    parser.add_argument("--budget", type=int, default=800)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--fail-on-gates", action="store_true")
    args = parser.parse_args(argv)

    corpus_path = Path(args.corpus).resolve()
    tasks = load_adaptive_tasks(corpus_path)
    if args.limit is not None:
        tasks = tasks[: max(0, args.limit)]

    baseline = StrongBaselineAdapter()
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="csegraph-adaptive-benchmark-") as tmp:
        scratch = Path(tmp)
        indexed: dict[Path, Path] = {}
        for task in tasks:
            repo = (REPO_ROOT / task.repo).resolve()
            observed_commit = _git_commit(repo)
            db = indexed.get(repo)
            if db is None:
                db = scratch / f"repo-{len(indexed)}.db"
                IndexService(db).index(repo, profile="auto")
                indexed[repo] = db

            baseline_result = baseline.retrieve(
                repo,
                task.task,
                target=task.target,
                token_budget=args.budget,
            )
            adaptive = ContextService(db).retrieve(
                ContextRequest(
                    repo=str(repo),
                    task=task.task,
                    target=task.target,
                    token_budget=args.budget,
                )
            )
            adaptive_payload = to_dict(adaptive)
            baseline_paths = {item.path for item in baseline_result.slices}
            adaptive_paths = {item.path for item in adaptive.slices}
            expected = set(task.expected_locations)
            permitted = set(task.permitted_files)
            results.append(
                {
                    "id": task.id,
                    "category": task.category,
                    "repo": task.repo,
                    "commit": task.commit,
                    "observed_commit": observed_commit,
                    "commit_matches": observed_commit == task.commit,
                    "expected_locations": sorted(expected),
                    "baseline": {
                        "tokens": baseline_result.usage["tokens"],
                        "latency_ms": baseline_result.usage["latency_ms"],
                        "tool_calls": baseline_result.usage["tool_calls"],
                        "paths": sorted(baseline_paths),
                        "recall": _recall(baseline_paths, expected),
                        "precision": _precision(baseline_paths, permitted),
                    },
                    "adaptive": {
                        "status": adaptive_payload["status"],
                        "tokens": adaptive_payload["usage"]["tokens"],
                        "latency_ms": adaptive_payload["usage"]["latency_ms"],
                        "tool_calls": 1,
                        "cache": adaptive_payload["usage"]["cache"],
                        "paths": sorted(adaptive_paths),
                        "recall": _recall(adaptive_paths, expected),
                        "precision": _precision(adaptive_paths, permitted),
                    },
                }
            )

    summary = _summary(results, args.budget)
    payload = {
        "schema_version": "csegraph-adaptive-retrieval-report-v1",
        "corpus": str(corpus_path),
        "token_budget": args.budget,
        "summary": summary,
        "tasks": results,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    if args.fail_on_gates and not summary["release_gates_passed"]:
        return 1
    return 0


def _git_commit(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


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


def _summary(results: list[dict[str, Any]], token_budget: int) -> dict[str, Any]:
    if not results:
        return {
            "task_count": 0,
            "release_gates_passed": False,
        }
    adaptive = [item["adaptive"] for item in results]
    baseline = [item["baseline"] for item in results]
    adaptive_tokens = [int(item["tokens"]) for item in adaptive]
    baseline_tokens = [int(item["tokens"]) for item in baseline]
    adaptive_latency = [float(item["latency_ms"]) for item in adaptive]
    baseline_latency = [float(item["latency_ms"]) for item in baseline]
    target_rate = sum(item["status"] == "ready" for item in adaptive) / len(adaptive)
    recall = statistics.mean(float(item["recall"]) for item in adaptive)
    precision = statistics.mean(float(item["precision"]) for item in adaptive)
    within_budget = all(tokens <= token_budget for tokens in adaptive_tokens)
    token_ratio = statistics.median(adaptive_tokens) / max(
        1.0, statistics.median(baseline_tokens)
    )
    latency_delta = _p95(adaptive_latency) - _p95(baseline_latency)
    gates = {
        "within_budget": within_budget,
        "target_ready_rate_at_least_95pct": target_rate >= 0.95,
        "slice_recall_at_least_95pct": recall >= 0.95,
        "slice_precision_at_least_70pct": precision >= 0.70,
        "median_tokens_no_greater_than_baseline": token_ratio <= 1.0,
        "p95_latency_delta_at_most_150ms": latency_delta <= 150.0,
    }
    return {
        "task_count": len(results),
        "target_ready_rate": round(target_rate, 4),
        "adaptive_recall": round(recall, 4),
        "adaptive_precision": round(precision, 4),
        "adaptive_median_tokens": statistics.median(adaptive_tokens),
        "baseline_median_tokens": statistics.median(baseline_tokens),
        "adaptive_to_baseline_token_ratio": round(token_ratio, 4),
        "adaptive_p95_latency_ms": round(_p95(adaptive_latency), 3),
        "baseline_p95_latency_ms": round(_p95(baseline_latency), 3),
        "p95_latency_delta_ms": round(latency_delta, 3),
        "gates": gates,
        "release_gates_passed": all(gates.values()),
    }


def _p95(values: list[float]) -> float:
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(0.95 * (len(ordered) - 1))))
    return ordered[index]


if __name__ == "__main__":
    raise SystemExit(main())
