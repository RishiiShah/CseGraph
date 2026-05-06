import argparse
import os

from benchmark_repeated import run_repeated_benchmark, save_outputs as save_repeated_outputs
from benchmark_sandboxes import run_benchmark, save_outputs as save_benchmark_outputs
from compare_baselines import run_comparison
from report_plots import generate_plots


DEFAULT_SANDBOX_ROOT = "sandboxes"
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
    parser.add_argument(
        "--skip-codegen",
        action="store_true",
        help="Skip LLM code generation in baseline comparison.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=20,
        help="Top-K for StaticRAG in baseline comparison.",
    )
    parser.add_argument(
        "--num-targets",
        type=int,
        default=3,
        help="Number of targets to evaluate per sandbox in baseline comparison (default: 3).",
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

    # --- Baseline comparison ---
    baseline_json = os.path.join(args.output_dir, "baseline_comparison.json")
    baseline_csv = os.path.join(args.output_dir, "baseline_comparison.csv")
    baseline_summary = os.path.join(args.output_dir, "baseline_summary.json")

    print("\nRunning baseline comparison (adaptive / full_context / static_rag) …")
    run_comparison(
        sandbox_root=args.sandbox_root,
        output_dir=args.output_dir,
        top_k=args.top_k,
        skip_codegen=args.skip_codegen,
        num_targets=args.num_targets,
    )

    generate_plots(
        output_csv,
        output_json,
        summary_json,
        args.plots_dir,
        comparison_csv=baseline_csv,
        comparison_summary_json=baseline_summary,
    )

    print("Full report generation complete")
    print(f"- Single-run JSON: {output_json}")
    print(f"- Single-run CSV: {output_csv}")
    print(f"- Repeated runs JSON: {runs_json}")
    print(f"- Repeated summary JSON: {summary_json}")
    print(f"- Repeated summary CSV: {summary_csv}")
    print(f"- Plots + summary: {args.plots_dir}")
    print(f"- Baseline comparison JSON: {baseline_json}")
    print(f"- Baseline comparison CSV: {baseline_csv}")
    print(f"- Baseline summary JSON: {baseline_summary}")


if __name__ == "__main__":
    main()
