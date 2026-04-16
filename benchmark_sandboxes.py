import argparse
import csv
import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

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


def _run_cse_metrics(
    graph,
    linking_agent,
    link_graph_path: str,
) -> Dict[str, Any]:
    """Run CompressionAgent + CSEAgent on an already-built graph.

    Parameters
    ----------
    graph:
        The LinkGraph already produced by LinkingAgent.build_graph().
    linking_agent:
        The LinkingAgent instance (used to serialize the graph).
    link_graph_path:
        Path to the already-written link-graph tempfile.

    Returns a dict of CSE metric fields, or a dict of None values on failure.
    """
    from agents.compression_agent import CompressionAgent
    from agents.cse_agent import CSEAgent
    from models.cse_result import SufficiencyQuery

    empty: Dict[str, Any] = {
        "node_summary_count": None,
        "cse_sufficient": None,
        "cse_dep_completeness": None,
        "cse_entity_coverage": None,
        "cse_semantic_overlap": None,
        "cse_model_confidence": None,
        "cse_expansion_rounds": None,
        "cse_context_nodes": None,
        "cse_raw_code_nodes": None,
    }

    try:
        compression_agent = CompressionAgent(link_graph_path)
        compressed = compression_agent.compress()

        with tempfile.TemporaryDirectory(prefix="benchmark_cse_") as tmp_dir:
            comp_tmp_path = os.path.join(tmp_dir, "compressed_graph.json")
            compression_agent.save_compressed(compressed, comp_tmp_path)

            cse = CSEAgent(link_graph_path, comp_tmp_path)
            target_id, target_file, auto_query = cse.pick_representative_target()

            query = SufficiencyQuery(
                query_text=auto_query,
                target_node_id=target_id,
                target_file_path=target_file,
            )
            result = cse.evaluate(query)

            return {
                "node_summary_count": len(compressed.node_summaries),
                "cse_sufficient": result.is_sufficient,
                "cse_dep_completeness": round(result.metrics.dependency_completeness, 4),
                "cse_entity_coverage": round(result.metrics.entity_coverage, 4),
                "cse_semantic_overlap": round(result.metrics.semantic_overlap, 4),
                "cse_model_confidence": round(result.metrics.model_confidence, 4),
                "cse_expansion_rounds": result.expansion_rounds,
                "cse_context_nodes": len(result.context_node_ids),
                "cse_raw_code_nodes": len(result.raw_code_nodes),
            }
    except Exception as exc:
        print(f"  [CSE] error: {exc}")
        return empty


def run_benchmark(sandbox_root: str, include_cse: bool = False) -> List[Dict[str, object]]:
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

        if include_cse:
            with tempfile.TemporaryDirectory(prefix="benchmark_link_") as tmp_dir:
                link_tmp_path = os.path.join(tmp_dir, "link_graph.json")
                linking_agent.save_graph(graph, link_tmp_path)
                cse_metrics = _run_cse_metrics(graph, linking_agent, link_tmp_path)
            metrics.update(cse_metrics)

        results.append(
            {
                "sandbox": sandbox_name,
                "path": sandbox_path,
                "metrics": metrics,
            }
        )

    return results


_BASE_CSV_COLUMNS = [
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

_CSE_CSV_COLUMNS = [
    "node_summary_count",
    "cse_sufficient",
    "cse_dep_completeness",
    "cse_entity_coverage",
    "cse_semantic_overlap",
    "cse_model_confidence",
    "cse_expansion_rounds",
    "cse_context_nodes",
    "cse_raw_code_nodes",
]


def save_outputs(results: List[Dict[str, object]], output_json: str, output_csv: str) -> None:
    os.makedirs(os.path.dirname(output_json), exist_ok=True)
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)

    with open(output_json, "w", encoding="utf-8") as json_file:
        json.dump(results, json_file, indent=4)

    # Determine whether any row has CSE columns and include them if so.
    has_cse = any(
        "node_summary_count" in result.get("metrics", {})
        for result in results
    )
    csv_columns = _BASE_CSV_COLUMNS + (_CSE_CSV_COLUMNS if has_cse else [])

    with open(output_csv, "w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=csv_columns, extrasaction="ignore")
        writer.writeheader()
        for result in results:
            row: Dict[str, Any] = {"sandbox": result["sandbox"]}
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
    parser.add_argument(
        "--include-cse",
        action="store_true",
        help=(
            "Additionally run CompressionAgent + CSEAgent on each sandbox "
            "and add CSE metrics to the output."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    benchmark_results = run_benchmark(args.sandbox_root, include_cse=args.include_cse)
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
