import argparse
import os

from benchmark_repeated import run_repeated_benchmark, save_outputs as save_repeated_outputs
from benchmark_sandboxes import run_benchmark, save_outputs as save_benchmark_outputs
from report_plots import generate_plots


DEFAULT_SANDBOX_ROOT = "tests/fixtures/sandboxes"
DEFAULT_OUTPUT_DIR = "data"
DEFAULT_PLOTS_DIR = "data/plots"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run full benchmark pipeline: single-run, repeated-runs, and plots."
    )
    parser.add_argument(
        "--sandbox-root",
        default=DEFAULT_SANDBOX_ROOT,
        help=f"Directory containing sandbox repos (default: {DEFAULT_SANDBOX_ROOT}).",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=5,
        help="Number of repeated benchmark runs (default: 5).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for generated benchmark files (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--plots-dir",
        default=DEFAULT_PLOTS_DIR,
        help=f"Directory for generated plots (default: {DEFAULT_PLOTS_DIR}).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    output_json = os.path.join(args.output_dir, "sandbox_benchmark.json")
    output_csv = os.path.join(args.output_dir, "sandbox_benchmark.csv")
    runs_json = os.path.join(args.output_dir, "sandbox_benchmark_runs.json")
    summary_json = os.path.join(args.output_dir, "sandbox_benchmark_summary.json")
    summary_csv = os.path.join(args.output_dir, "sandbox_benchmark_summary.csv")

    single_run = run_benchmark(args.sandbox_root)
    save_benchmark_outputs(single_run, output_json, output_csv)

    repeated_payload = run_repeated_benchmark(args.sandbox_root, args.repeats)
    save_repeated_outputs(repeated_payload, runs_json, summary_json, summary_csv)

    generate_plots(output_csv, output_json, summary_json, args.plots_dir)

    print("Full report generation complete")
    print(f"- Single-run JSON: {output_json}")
    print(f"- Single-run CSV: {output_csv}")
    print(f"- Repeated runs JSON: {runs_json}")
    print(f"- Repeated summary JSON: {summary_json}")
    print(f"- Repeated summary CSV: {summary_csv}")
    print(f"- Plots + summary: {args.plots_dir}")


if __name__ == "__main__":
    main()
