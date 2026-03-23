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


def generate_plots(
    input_csv: str,
    input_json: str,
    repeated_summary_json: str | None,
    output_dir: str,
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
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    generate_plots(
        args.input_csv,
        args.input_json,
        args.repeated_summary_json,
        args.output_dir,
    )
    print(f"Saved plots and summary under '{args.output_dir}'")