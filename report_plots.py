import argparse
import csv
import json
import os
from statistics import mean

import matplotlib.pyplot as plt


DEFAULT_INPUT_CSV = "data/sandbox_benchmark.csv"
DEFAULT_INPUT_JSON = "data/sandbox_benchmark.json"
DEFAULT_REPEATED_SUMMARY_JSON = "data/sandbox_benchmark_summary.json"
DEFAULT_OUTPUT_DIR = "data/plots"


def _read_csv_rows(csv_path: str) -> list[dict[str, str]]:
    with open(csv_path, "r", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))


def _read_json_rows(json_path: str) -> list[dict]:
    with open(json_path, "r", encoding="utf-8") as json_file:
        return json.load(json_file)


def _read_optional_json_rows(json_path: str | None) -> list[dict]:
    if not json_path:
        return []
    if not os.path.exists(json_path):
        return []
    return _read_json_rows(json_path)


def _to_float(rows: list[dict[str, str]], key: str) -> list[float]:
    return [float(row[key]) for row in rows]


def _to_str(rows: list[dict[str, str]], key: str) -> list[str]:
    return [row[key] for row in rows]


def _bar_plot(
    names: list[str],
    values: list[float],
    title: str,
    ylabel: str,
    output_path: str,
) -> None:
    plt.figure(figsize=(10, 5))
    bars = plt.bar(names, values, color="#1f77b4")
    plt.title(title)
    plt.ylabel(ylabel)
    plt.xticks(rotation=20, ha="right")
    plt.grid(axis="y", linestyle="--", alpha=0.4)
    for bar, value in zip(bars, values):
        plt.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{value:.2f}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def _scatter_plot(
    x_values: list[float],
    y_values: list[float],
    labels: list[str],
    title: str,
    xlabel: str,
    ylabel: str,
    output_path: str,
) -> None:
    plt.figure(figsize=(7, 6))
    plt.scatter(x_values, y_values, s=80, color="#ff7f0e")
    for x_value, y_value, label in zip(x_values, y_values, labels):
        plt.text(x_value + 0.02, y_value + 0.01, label, fontsize=9)
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.grid(linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def _strategy_scatter_plot(
    x_values: list[float],
    y_values: list[float],
    labels: list[str],
    title: str,
    xlabel: str,
    ylabel: str,
    output_path: str,
) -> None:
    """Scatter plot with one labeled, strategy-coloured point per strategy."""
    plt.figure(figsize=(7, 6))
    x_range = (max(x_values) - min(x_values)) if len(x_values) > 1 else 1.0
    offset = x_range * 0.02 if x_range > 0 else 1.0
    for x, y, label in zip(x_values, y_values, labels):
        color = STRATEGY_COLORS.get(label, "#888888")
        plt.scatter([x], [y], s=150, color=color, zorder=3)
        plt.text(x + offset, y, label, fontsize=10, color=color)
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.grid(linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def _stacked_edge_plot(rows: list[dict[str, str]], output_path: str) -> None:
    sandboxes = _to_str(rows, "sandbox")
    import_edges = _to_float(rows, "import_edges")
    call_edges = _to_float(rows, "call_edges")
    contains_edges = _to_float(rows, "contains_edges")

    plt.figure(figsize=(10, 5))
    plt.bar(sandboxes, import_edges, label="imports", color="#4c78a8")
    plt.bar(sandboxes, call_edges, bottom=import_edges, label="calls", color="#f58518")

    bottom = [i + c for i, c in zip(import_edges, call_edges)]
    plt.bar(sandboxes, contains_edges, bottom=bottom, label="contains", color="#54a24b")

    plt.title("Edge Composition by Sandbox")
    plt.ylabel("Edge Count")
    plt.xticks(rotation=20, ha="right")
    plt.legend()
    plt.grid(axis="y", linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def _errorbar_plot(
    labels: list[str],
    means: list[float],
    ci95: list[float],
    title: str,
    ylabel: str,
    output_path: str,
) -> None:
    plt.figure(figsize=(10, 5))
    x_positions = list(range(len(labels)))
    plt.errorbar(
        x_positions,
        means,
        yerr=ci95,
        fmt="o",
        capsize=6,
        color="#2ca02c",
        ecolor="#2ca02c",
        elinewidth=1.5,
    )
    plt.xticks(x_positions, labels, rotation=20, ha="right")
    plt.title(title)
    plt.ylabel(ylabel)
    plt.grid(axis="y", linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def _write_report_summary(
    rows: list[dict[str, str]],
    json_rows: list[dict],
    repeated_rows: list[dict],
    output_path: str,
) -> None:
    files = _to_float(rows, "file_count")
    symbols = _to_float(rows, "symbol_count")
    edges = _to_float(rows, "edge_count")
    import_ratio = _to_float(rows, "resolved_import_ratio")

    best_import = max(rows, key=lambda row: float(row["resolved_import_ratio"]))
    densest = max(rows, key=lambda row: float(row["edges_per_node"]))

    with open(output_path, "w", encoding="utf-8") as summary_file:
        summary_file.write("# Benchmark Summary\n\n")
        summary_file.write(f"Sandboxes analyzed: {len(rows)}\n\n")
        summary_file.write("## Aggregate Stats\n")
        summary_file.write(f"- Mean files per sandbox: {mean(files):.2f}\n")
        summary_file.write(f"- Mean symbols per sandbox: {mean(symbols):.2f}\n")
        summary_file.write(f"- Mean edges per sandbox: {mean(edges):.2f}\n")
        summary_file.write(f"- Mean resolved import ratio: {mean(import_ratio):.4f}\n\n")
        summary_file.write("## Highlights\n")
        summary_file.write(
            f"- Best import resolution: {best_import['sandbox']} ({best_import['resolved_import_ratio']})\n"
        )
        summary_file.write(
            f"- Highest edge density: {densest['sandbox']} ({densest['edges_per_node']})\n\n"
        )

        summary_file.write("## Data Integrity\n")
        summary_file.write(
            "- JSON entries match CSV rows: "
            f"{'yes' if len(json_rows) == len(rows) else 'no'}\n"
        )

        if repeated_rows:
            summary_file.write("\n## Repeated-Run Stats\n")
            repeats = repeated_rows[0].get("repeats", "unknown")
            summary_file.write(f"- Repeats per sandbox: {repeats}\n")

            fastest = min(
                repeated_rows,
                key=lambda row: row["metrics"]["total_seconds"]["mean"],
            )
            most_stable = min(
                repeated_rows,
                key=lambda row: row["metrics"]["total_seconds"]["std"],
            )
            summary_file.write(
                "- Fastest mean runtime: "
                f"{fastest['sandbox']} ({fastest['metrics']['total_seconds']['mean']} s)\n"
            )
            summary_file.write(
                "- Most stable runtime (lowest std): "
                f"{most_stable['sandbox']} ({most_stable['metrics']['total_seconds']['std']} s)\n"
            )


STRATEGY_COLORS = {
    "adaptive": "#1f77b4",
    "full_context": "#ff7f0e",
    "static_rag": "#2ca02c",
}


def _grouped_bar_plot(
    metric_names: list[str],
    series_dict: dict[str, list[float | None]],
    title: str,
    ylabel: str,
    output_path: str,
    y_max: float | None = 1.0,
) -> None:
    import numpy as np

    strategies = list(series_dict.keys())
    n_metrics = len(metric_names)
    n_strategies = len(strategies)
    x = np.arange(n_metrics)
    total_width = 0.7
    bar_width = total_width / n_strategies

    plt.figure(figsize=(10, 5))
    for i, strategy in enumerate(strategies):
        values = series_dict[strategy]
        offsets = x - total_width / 2 + bar_width / 2 + i * bar_width
        bars = plt.bar(
            offsets,
            [v if v is not None else 0 for v in values],
            width=bar_width,
            label=strategy,
            color=STRATEGY_COLORS.get(strategy, "#888888"),
        )
        for bar, value in zip(bars, values):
            if value is not None:
                plt.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height(),
                    f"{value:.2f}",
                    ha="center",
                    va="bottom",
                    fontsize=7,
                )

    plt.xticks(x, metric_names, rotation=15, ha="right")
    plt.title(title)
    plt.ylabel(ylabel)
    if y_max is not None:
        plt.ylim(0, y_max)
    plt.legend()
    plt.grid(axis="y", linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def generate_baseline_plots(
    comparison_csv: str,
    summary_json: str,
    output_dir: str,
) -> None:
    os.makedirs(output_dir, exist_ok=True)

    # Load summary JSON.  _compute_summary now returns
    # {"global": {strategy: {...}}, "per_sandbox": {...}}.
    # Fall back to the flat structure from older runs so old files still work.
    raw: dict = {}
    if not summary_json or not os.path.exists(summary_json):
        summary = {}
    else:
        with open(summary_json, "r", encoding="utf-8") as f:
            raw = json.load(f)
        summary = raw.get("global", raw)

    # Load comparison CSV rows
    if comparison_csv and os.path.exists(comparison_csv):
        csv_rows = _read_csv_rows(comparison_csv)
    else:
        csv_rows = []

    strategies = ["adaptive", "full_context", "static_rag"]

    # --- Plot 1: baseline_context_nodes.png ---
    node_names = []
    node_values = []
    for s in strategies:
        if s in summary and summary[s].get("context_node_count") is not None:
            node_names.append(s)
            node_values.append(float(summary[s]["context_node_count"]))
    if node_names:
        _bar_plot(
            node_names,
            node_values,
            "Mean Context Node Count by Strategy",
            "Mean Nodes",
            os.path.join(output_dir, "baseline_context_nodes.png"),
        )

    # --- Plot 2: baseline_token_efficiency.png ---
    token_names = []
    token_values = []
    for s in strategies:
        if s in summary and summary[s].get("prompt_tokens") is not None:
            token_names.append(s)
            token_values.append(float(summary[s]["prompt_tokens"]))
    if token_names:
        codegen_skipped = any(
            summary.get(s, {}).get("prompt_tokens") is None for s in strategies
        )
        title = "Mean Prompt Tokens by Strategy"
        if codegen_skipped:
            title += " (codegen skipped for some strategies)"
        _bar_plot(
            token_names,
            token_values,
            title,
            "Mean Prompt Tokens",
            os.path.join(output_dir, "baseline_token_efficiency.png"),
        )

    # --- Plot 3: baseline_cse_metrics.png ---
    cse_metric_keys = [
        "dep_completeness",
        "entity_coverage",
        "semantic_overlap",
        "model_confidence",
    ]
    cse_series: dict[str, list[float | None]] = {}
    any_cse_data = False
    for s in strategies:
        row_vals: list[float | None] = []
        for mk in cse_metric_keys:
            val = summary.get(s, {}).get(mk)
            row_vals.append(float(val) if val is not None else None)
        cse_series[s] = row_vals
        if any(v is not None for v in row_vals):
            any_cse_data = True

    if any_cse_data:
        _grouped_bar_plot(
            cse_metric_keys,
            cse_series,
            "CSE Metrics by Strategy",
            "Score (0–1)",
            os.path.join(output_dir, "baseline_cse_metrics.png"),
        )

    # --- Plot 4: baseline_compile_rate.png ---
    compile_names = []
    compile_values = []
    for s in strategies:
        val = summary.get(s, {}).get("compile_success_rate")
        if val is not None:
            compile_names.append(s)
            compile_values.append(float(val))
    if compile_names:
        _bar_plot(
            compile_names,
            compile_values,
            "Compile Success Rate by Strategy",
            "Compile Success Rate",
            os.path.join(output_dir, "baseline_compile_rate.png"),
        )

    # --- Plot 5: baseline_unit_test_pass_rate.png ---
    ut_names = []
    ut_values = []
    for s in strategies:
        val = summary.get(s, {}).get("unit_test_pass_rate")
        if val is not None:
            ut_names.append(s)
            ut_values.append(float(val))
    if ut_names:
        _bar_plot(
            ut_names,
            ut_values,
            "Unit Test Pass Rate by Strategy (Correctness)",
            "Pass Rate",
            os.path.join(output_dir, "baseline_unit_test_pass_rate.png"),
        )

    # --- Plot 6: baseline_expansion_rounds.png ---
    exp_names = []
    exp_values = []
    for s in strategies:
        val = summary.get(s, {}).get("expansion_rounds")
        if val is not None:
            exp_names.append(s)
            exp_values.append(float(val))
    if exp_names:
        _bar_plot(
            exp_names,
            exp_values,
            "Mean Expansion Rounds by Strategy",
            "Mean Rounds",
            os.path.join(output_dir, "baseline_expansion_rounds.png"),
        )

    # --- Plots 7 & 8: per-sandbox breakdowns ---
    per_sandbox: dict = raw.get("per_sandbox", {})
    if per_sandbox:
        sandbox_names = sorted(per_sandbox.keys())

        # Plot 7: context_node_count per sandbox × strategy
        node_series: dict[str, list[float | None]] = {}
        for s in strategies:
            node_series[s] = [
                float(per_sandbox[sb].get(s, {}).get("context_node_count"))
                if per_sandbox[sb].get(s, {}).get("context_node_count") is not None
                else None
                for sb in sandbox_names
            ]
        if any(any(v is not None for v in vals) for vals in node_series.values()):
            _grouped_bar_plot(
                sandbox_names,
                node_series,
                "Context Nodes per Sandbox by Strategy",
                "Context Nodes",
                os.path.join(output_dir, "per_sandbox_context_nodes.png"),
                y_max=None,
            )

        # Plot 8: entity_coverage per sandbox × strategy
        cov_series: dict[str, list[float | None]] = {}
        for s in strategies:
            cov_series[s] = [
                float(per_sandbox[sb].get(s, {}).get("entity_coverage"))
                if per_sandbox[sb].get(s, {}).get("entity_coverage") is not None
                else None
                for sb in sandbox_names
            ]
        if any(any(v is not None for v in vals) for vals in cov_series.values()):
            _grouped_bar_plot(
                sandbox_names,
                cov_series,
                "Entity Coverage per Sandbox by Strategy",
                "Entity Coverage Score",
                os.path.join(output_dir, "per_sandbox_entity_coverage.png"),
            )

        # Plot 9: unit_test_pass_rate per sandbox × strategy
        ut_series: dict[str, list[float | None]] = {}
        for s in strategies:
            ut_series[s] = [
                float(per_sandbox[sb].get(s, {}).get("unit_test_pass_rate"))
                if per_sandbox[sb].get(s, {}).get("unit_test_pass_rate") is not None
                else None
                for sb in sandbox_names
            ]
        if any(any(v is not None for v in vals) for vals in ut_series.values()):
            _grouped_bar_plot(
                sandbox_names,
                ut_series,
                "Unit Test Pass Rate per Sandbox by Strategy",
                "Pass Rate",
                os.path.join(output_dir, "per_sandbox_unit_test_pass_rate.png"),
            )

    # Plot 10: efficiency scatter — mean prompt tokens vs mean unit test pass rate
    eff_x: list[float] = []
    eff_y: list[float] = []
    eff_labels: list[str] = []
    for s in strategies:
        pt = summary.get(s, {}).get("prompt_tokens")
        ut = summary.get(s, {}).get("unit_test_pass_rate")
        if pt is not None and ut is not None:
            eff_x.append(float(pt))
            eff_y.append(float(ut))
            eff_labels.append(s)
    if eff_x:
        _strategy_scatter_plot(
            eff_x,
            eff_y,
            eff_labels,
            "Token Efficiency vs Correctness by Strategy",
            "Mean Prompt Tokens",
            "Mean Unit Test Pass Rate",
            os.path.join(output_dir, "efficiency_scatter.png"),
        )


def generate_plots(
    input_csv: str,
    input_json: str,
    repeated_summary_json: str | None,
    output_dir: str,
    comparison_csv: str | None = None,
    comparison_summary_json: str | None = None,
) -> None:
    os.makedirs(output_dir, exist_ok=True)
    rows = _read_csv_rows(input_csv)
    json_rows = _read_json_rows(input_json)
    repeated_rows = _read_optional_json_rows(repeated_summary_json)

    names = _to_str(rows, "sandbox")
    symbol_counts = _to_float(rows, "symbol_count")
    edge_counts = _to_float(rows, "edge_count")
    import_ratio = _to_float(rows, "resolved_import_ratio")
    edges_per_node = _to_float(rows, "edges_per_node")

    _bar_plot(
        names,
        symbol_counts,
        "Symbol Count by Sandbox",
        "Symbols",
        os.path.join(output_dir, "symbol_count_bar.png"),
    )

    _bar_plot(
        names,
        import_ratio,
        "Import Resolution Ratio by Sandbox",
        "Resolved Import Ratio",
        os.path.join(output_dir, "import_resolution_bar.png"),
    )

    _stacked_edge_plot(rows, os.path.join(output_dir, "edge_composition_stacked.png"))

    _scatter_plot(
        symbol_counts,
        edge_counts,
        names,
        "Graph Size Relationship",
        "Symbol Count",
        "Edge Count",
        os.path.join(output_dir, "symbol_vs_edge_scatter.png"),
    )

    _scatter_plot(
        import_ratio,
        edges_per_node,
        names,
        "Import Resolution vs Graph Density",
        "Resolved Import Ratio",
        "Edges per Node",
        os.path.join(output_dir, "import_ratio_vs_density_scatter.png"),
    )

    if repeated_rows:
        labels = [row["sandbox"] for row in repeated_rows]
        runtime_means = [row["metrics"]["total_seconds"]["mean"] for row in repeated_rows]
        runtime_ci95 = [row["metrics"]["total_seconds"]["ci95"] for row in repeated_rows]
        import_means = [
            row["metrics"]["resolved_import_ratio"]["mean"] for row in repeated_rows
        ]
        import_ci95 = [
            row["metrics"]["resolved_import_ratio"]["ci95"] for row in repeated_rows
        ]

        _errorbar_plot(
            labels,
            runtime_means,
            runtime_ci95,
            "Runtime Across Repeated Runs (95% CI)",
            "Total Runtime (s)",
            os.path.join(output_dir, "runtime_ci95_errorbar.png"),
        )

        _errorbar_plot(
            labels,
            import_means,
            import_ci95,
            "Import Resolution Across Repeated Runs (95% CI)",
            "Resolved Import Ratio",
            os.path.join(output_dir, "import_ratio_ci95_errorbar.png"),
        )

    _write_report_summary(
        rows,
        json_rows,
        repeated_rows,
        os.path.join(output_dir, "benchmark_summary.md"),
    )

    if (
        comparison_csv
        and comparison_summary_json
        and os.path.exists(comparison_csv)
        and os.path.exists(comparison_summary_json)
    ):
        generate_baseline_plots(comparison_csv, comparison_summary_json, output_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate benchmark plots and report summary from sandbox outputs."
    )
    parser.add_argument(
        "--input-csv",
        default=DEFAULT_INPUT_CSV,
        help=f"Input CSV path (default: {DEFAULT_INPUT_CSV}).",
    )
    parser.add_argument(
        "--input-json",
        default=DEFAULT_INPUT_JSON,
        help=f"Input JSON path (default: {DEFAULT_INPUT_JSON}).",
    )
    parser.add_argument(
        "--repeated-summary-json",
        default=DEFAULT_REPEATED_SUMMARY_JSON,
        help=(
            "Optional repeated-summary JSON path for CI/error-bar plots "
            f"(default: {DEFAULT_REPEATED_SUMMARY_JSON})."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory for plots and summary (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--baseline-csv",
        default=None,
        help="Path to baseline_comparison.csv (optional).",
    )
    parser.add_argument(
        "--baseline-summary",
        default=None,
        help="Path to baseline_summary.json (optional).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    generate_plots(
        args.input_csv,
        args.input_json,
        args.repeated_summary_json,
        args.output_dir,
        comparison_csv=args.baseline_csv,
        comparison_summary_json=args.baseline_summary,
    )
    if args.baseline_csv and args.baseline_summary:
        generate_baseline_plots(args.baseline_csv, args.baseline_summary, args.output_dir)
    print(f"Saved plots and summary under '{args.output_dir}'")