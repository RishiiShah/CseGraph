import argparse
import csv
import json
import math
import os
import subprocess
import sys
import tempfile
from statistics import mean, stdev
from typing import Dict, List


DEFAULT_SANDBOX_ROOT = "sandboxes"
DEFAULT_REPEATS = 5
DEFAULT_RUNS_JSON = "data/sandbox_benchmark_runs.json"
DEFAULT_SUMMARY_JSON = "data/sandbox_benchmark_summary.json"
DEFAULT_SUMMARY_CSV = "data/sandbox_benchmark_summary.csv"


def _numeric_metric_names(results: List[Dict[str, object]]) -> List[str]:
    if not results:
        return []
    return sorted(results[0]["metrics"].keys())


def _metric_stats(values: List[float]) -> Dict[str, float]:
    avg = mean(values)
    std = stdev(values) if len(values) > 1 else 0.0
    ci95 = 1.96 * std / math.sqrt(len(values)) if len(values) > 1 else 0.0
    return {
        "mean": round(avg, 6),
        "std": round(std, 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "ci95": round(ci95, 6),
    }


def run_repeated_benchmark(sandbox_root: str, repeats: int) -> Dict[str, object]:
    if repeats < 1:
        raise ValueError("repeats must be >= 1")

    run_records: List[Dict[str, object]] = []
    grouped: Dict[str, Dict[str, List[float]]] = {}

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    for run_index in range(1, repeats + 1):
        with tempfile.TemporaryDirectory(prefix="benchmark_run_") as temp_dir:
            run_json_path = os.path.join(temp_dir, f"run_{run_index}.json")
            run_csv_path = os.path.join(temp_dir, f"run_{run_index}.csv")

            command = [
                sys.executable,
                os.path.join(repo_root, "scripts", "benchmarks", "benchmark_sandboxes.py"),
                "--sandbox-root",
                sandbox_root,
                "--output-json",
                run_json_path,
                "--output-csv",
                run_csv_path,
            ]
            subprocess.run(
                command,
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )

            with open(run_json_path, "r", encoding="utf-8") as run_file:
                results = json.load(run_file)

        run_records.append({"run": run_index, "results": results})

        for result in results:
            sandbox = result["sandbox"]
            grouped.setdefault(sandbox, {})
            for metric_name, metric_value in result["metrics"].items():
                grouped[sandbox].setdefault(metric_name, [])
                grouped[sandbox][metric_name].append(float(metric_value))

    summary: List[Dict[str, object]] = []
    for sandbox in sorted(grouped.keys()):
        metrics_summary = {
            metric_name: _metric_stats(values)
            for metric_name, values in sorted(grouped[sandbox].items())
        }
        summary.append({"sandbox": sandbox, "repeats": repeats, "metrics": metrics_summary})

    return {
        "repeats": repeats,
        "sandbox_root": os.path.abspath(sandbox_root),
        "runs": run_records,
        "summary": summary,
    }


def save_outputs(payload: Dict[str, object], runs_json: str, summary_json: str, summary_csv: str) -> None:
    os.makedirs(os.path.dirname(runs_json), exist_ok=True)
    os.makedirs(os.path.dirname(summary_json), exist_ok=True)
    os.makedirs(os.path.dirname(summary_csv), exist_ok=True)

    with open(runs_json, "w", encoding="utf-8") as runs_file:
        json.dump(payload["runs"], runs_file, indent=4)

    summary_rows = payload["summary"]
    with open(summary_json, "w", encoding="utf-8") as summary_file:
        json.dump(summary_rows, summary_file, indent=4)

    metric_names = set()
    for row in summary_rows:
        metric_names.update(row["metrics"].keys())

    csv_columns = ["sandbox", "metric", "mean", "std", "min", "max", "ci95", "repeats"]
    with open(summary_csv, "w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=csv_columns)
        writer.writeheader()
        for row in summary_rows:
            sandbox = row["sandbox"]
            repeats = row["repeats"]
            for metric_name in sorted(metric_names):
                metric_stats = row["metrics"].get(metric_name)
                if not metric_stats:
                    continue
                writer.writerow(
                    {
                        "sandbox": sandbox,
                        "metric": metric_name,
                        "mean": metric_stats["mean"],
                        "std": metric_stats["std"],
                        "min": metric_stats["min"],
                        "max": metric_stats["max"],
                        "ci95": metric_stats["ci95"],
                        "repeats": repeats,
                    }
                )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run repeated benchmark experiments and compute aggregate statistics."
    )
    parser.add_argument(
        "--sandbox-root",
        default=DEFAULT_SANDBOX_ROOT,
        help=f"Directory containing sandbox repos (default: {DEFAULT_SANDBOX_ROOT}).",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=DEFAULT_REPEATS,
        help=f"Number of repeated runs (default: {DEFAULT_REPEATS}).",
    )
    parser.add_argument(
        "--runs-json",
        default=DEFAULT_RUNS_JSON,
        help=f"Path to save all run records (default: {DEFAULT_RUNS_JSON}).",
    )
    parser.add_argument(
        "--summary-json",
        default=DEFAULT_SUMMARY_JSON,
        help=f"Path to save summary JSON (default: {DEFAULT_SUMMARY_JSON}).",
    )
    parser.add_argument(
        "--summary-csv",
        default=DEFAULT_SUMMARY_CSV,
        help=f"Path to save summary CSV (default: {DEFAULT_SUMMARY_CSV}).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    payload = run_repeated_benchmark(args.sandbox_root, args.repeats)
    save_outputs(payload, args.runs_json, args.summary_json, args.summary_csv)

    print(f"Completed {args.repeats} repeated benchmark runs")
    for row in payload["summary"]:
        metric = row["metrics"]["total_seconds"]
        print(
            f"- {row['sandbox']}: total_seconds mean={metric['mean']}, "
            f"std={metric['std']}, ci95={metric['ci95']}"
        )
    print(f"Saved run-level JSON to '{args.runs_json}'")
    print(f"Saved summary JSON to '{args.summary_json}'")
    print(f"Saved summary CSV to '{args.summary_csv}'")
