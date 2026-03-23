import argparse
import csv
import json
import os
import time
from typing import Dict, List

from agents.ingestion_agent import IngestionAgent
from agents.linking_agent import LinkingAgent


DEFAULT_SANDBOX_ROOT = "tests/fixtures/sandboxes"
DEFAULT_OUTPUT_JSON = "data/sandbox_benchmark.json"
DEFAULT_OUTPUT_CSV = "data/sandbox_benchmark.csv"


def _compute_metrics(parsed_files, graph) -> Dict[str, float]:
    node_count = len(graph.nodes)
    edge_count = len(graph.edges)
    import_edge_count = sum(1 for edge in graph.edges if edge.relation == "imports")
    call_edge_count = sum(1 for edge in graph.edges if edge.relation == "calls")
    contains_edge_count = sum(1 for edge in graph.edges if edge.relation == "contains")

    total_import_statements = sum(len(file_node.imports) for file_node in parsed_files)
    resolved_import_ratio = (
        import_edge_count / total_import_statements if total_import_statements else 0.0
    )

    symbols_per_file = (
        graph.summary.symbol_count / graph.summary.file_count
        if graph.summary.file_count
        else 0.0
    )

    return {
        "file_count": graph.summary.file_count,
        "symbol_count": graph.summary.symbol_count,
        "node_count": node_count,
        "edge_count": edge_count,
        "import_edges": import_edge_count,
        "call_edges": call_edge_count,
        "contains_edges": contains_edge_count,
        "imports_in_source": total_import_statements,
        "resolved_import_ratio": round(resolved_import_ratio, 4),
        "symbols_per_file": round(symbols_per_file, 4),
        "edges_per_node": round(edge_count / node_count, 4) if node_count else 0.0,
    }


def run_benchmark(sandbox_root: str) -> List[Dict[str, object]]:
    abs_root = os.path.abspath(sandbox_root)
    sandbox_names = sorted(
        name
        for name in os.listdir(abs_root)
        if os.path.isdir(os.path.join(abs_root, name))
    )

    results: List[Dict[str, object]] = []
    for sandbox_name in sandbox_names:
        sandbox_path = os.path.join(abs_root, sandbox_name)

        start_ingestion = time.perf_counter()
        ingestion_agent = IngestionAgent(sandbox_path)
        parsed_files = ingestion_agent.ingest_repository()
        ingestion_seconds = time.perf_counter() - start_ingestion

        start_linking = time.perf_counter()
        linking_agent = LinkingAgent(sandbox_path)
        # Build graph from the sandbox path only; no in-memory preloaded files are passed in.
        graph = linking_agent.build_graph()
        linking_seconds = time.perf_counter() - start_linking

        metrics = _compute_metrics(parsed_files, graph)
        metrics.update(
            {
                "ingestion_seconds": round(ingestion_seconds, 6),
                "linking_seconds": round(linking_seconds, 6),
                "total_seconds": round(ingestion_seconds + linking_seconds, 6),
            }
        )
        results.append(
            {
                "sandbox": sandbox_name,
                "path": sandbox_path,
                "metrics": metrics,
            }
        )

    return results


def save_outputs(results: List[Dict[str, object]], output_json: str, output_csv: str) -> None:
    os.makedirs(os.path.dirname(output_json), exist_ok=True)
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)

    with open(output_json, "w", encoding="utf-8") as json_file:
        json.dump(results, json_file, indent=4)

    csv_columns = [
        "sandbox",
        "file_count",
        "symbol_count",
        "node_count",
        "edge_count",
        "import_edges",
        "call_edges",
        "contains_edges",
        "imports_in_source",
        "resolved_import_ratio",
        "symbols_per_file",
        "edges_per_node",
        "ingestion_seconds",
        "linking_seconds",
        "total_seconds",
    ]

    with open(output_csv, "w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=csv_columns)
        writer.writeheader()
        for result in results:
            row = {"sandbox": result["sandbox"]}
            row.update(result["metrics"])
            writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ingestion/linking benchmark across sandbox repositories."
    )
    parser.add_argument(
        "--sandbox-root",
        default=DEFAULT_SANDBOX_ROOT,
        help=f"Directory containing sandbox repos (default: {DEFAULT_SANDBOX_ROOT}).",
    )
    parser.add_argument(
        "--output-json",
        default=DEFAULT_OUTPUT_JSON,
        help=f"JSON report path (default: {DEFAULT_OUTPUT_JSON}).",
    )
    parser.add_argument(
        "--output-csv",
        default=DEFAULT_OUTPUT_CSV,
        help=f"CSV report path (default: {DEFAULT_OUTPUT_CSV}).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    benchmark_results = run_benchmark(args.sandbox_root)
    save_outputs(benchmark_results, args.output_json, args.output_csv)

    print(f"Benchmarked {len(benchmark_results)} sandboxes")
    for result in benchmark_results:
        metrics = result["metrics"]
        print(
            f"- {result['sandbox']}: "
            f"files={metrics['file_count']}, "
            f"symbols={metrics['symbol_count']}, "
            f"edges={metrics['edge_count']}, "
            f"resolved_import_ratio={metrics['resolved_import_ratio']}"
        )
    print(f"Saved JSON report to '{args.output_json}'")
    print(f"Saved CSV report to '{args.output_csv}'")
