from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from csegraph._core.benchmark import BenchmarkService
from csegraph._core.core.serializer import to_dict

DEFAULT_MIN_OVERALL_HIT_RATE = 0.85
DEFAULT_MIN_TASK_PASS_RATE = 0.60
DEFAULT_MAX_FAILED_TASKS = 2
DEFAULT_MAX_AVG_CONTEXT_TOKENS = 1300
DEFAULT_MAX_AVG_RESPONSE_BYTES = 17000
DEFAULT_MAX_RETURNED_NODE_COUNT = 18
DEFAULT_MIN_TASK_HIT_RATE = 0.70


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo = Path(args.repo).resolve()
    corpus = Path(args.corpus).resolve()

    with tempfile.TemporaryDirectory(prefix="csegraph-benchmark-") as tmp:
        db_path = Path(args.db).resolve() if args.db else Path(tmp) / "benchmark.db"
        result = BenchmarkService(db_path).run_corpus(repo, corpus, profile=args.profile)

    payload = to_dict(result)
    print(json.dumps(payload, indent=2, sort_keys=True))

    violations = _threshold_violations(payload, args)
    if violations:
        print("Benchmark regression thresholds failed:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1
    return 0


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
        description="Run context benchmark corpus and fail on regression thresholds.",
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


if __name__ == "__main__":
    raise SystemExit(main())
