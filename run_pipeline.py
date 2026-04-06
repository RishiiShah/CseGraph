import argparse
import os

from agents.ingestion_agent import IngestionAgent
from agents.linking_agent import LinkingAgent
from agents.compression_agent import CompressionAgent
from agents.cse_agent import CSEAgent
from models.cse_result import SufficiencyQuery


DEFAULT_SCAN_ROOT = "tests/fixtures/sandboxes"


def run_pipeline(root_dir: str, output_dir: str) -> None:
    abs_root = os.path.abspath(root_dir)
    abs_output_dir = os.path.abspath(output_dir)
    os.makedirs(abs_output_dir, exist_ok=True)

    # Step 1: Ingestion
    ingestion_agent = IngestionAgent(abs_root)
    parsed_files = ingestion_agent.ingest_repository()
    ingested_output = os.path.join(abs_output_dir, "ingested_data.json")
    ingestion_agent.save_to_json(parsed_files, ingested_output)

    # Step 2: Linking
    linking_agent = LinkingAgent(abs_root)
    graph = linking_agent.build_graph()
    graph_output = os.path.join(abs_output_dir, "link_graph.json")
    linking_agent.save_graph(graph, graph_output)

    # Step 3: Compression
    compressor = CompressionAgent(graph_output)
    compressed = compressor.compress()
    compressed_output = os.path.join(abs_output_dir, "compressed_graph.json")
    compressor.save_compressed(compressed, compressed_output)

    # Step 4: Context Sufficiency Estimation
    cse = CSEAgent(graph_output, compressed_output)
    target_id, target_file = cse.pick_representative_target()
    sample_query = SufficiencyQuery(
        query_text=f"Generate code related to {cse._node_lookup[target_id].name}",
        target_node_id=target_id,
        target_file_path=target_file,
    )
    cse_result = cse.evaluate(sample_query)
    cse_output = os.path.join(abs_output_dir, "cse_result.json")
    cse.save_result(cse_result, cse_output)

    # Summary
    print(f"\nIngestion complete: {len(parsed_files)} files -> '{ingested_output}'")
    print(
        "Linking complete: "
        f"{graph.summary.file_count} files, "
        f"{graph.summary.symbol_count} symbols, "
        f"{graph.summary.edge_count} edges -> '{graph_output}'"
    )
    print(
        "Compression complete: "
        f"{len(compressed.node_summaries)} summaries, "
        f"{len(compressed.context_slices)} context slices -> '{compressed_output}'"
    )
    print(
        f"CSE complete: sufficient={cse_result.is_sufficient}, "
        f"rounds={cse_result.expansion_rounds}/{cse_result.max_rounds}, "
        f"dep={cse_result.metrics.dependency_completeness:.0%}, "
        f"ent={cse_result.metrics.entity_coverage:.0%}, "
        f"sem={cse_result.metrics.semantic_overlap:.0%} "
        f"-> '{cse_output}'"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the full pipeline: Ingestion -> Linking -> Compression -> CSE."
    )
    parser.add_argument(
        "--root-dir",
        default=DEFAULT_SCAN_ROOT,
        help=(
            "Repository root to analyze "
            f"(default: {DEFAULT_SCAN_ROOT})."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where pipeline artifacts are written (default: data).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_pipeline(root_dir=args.root_dir, output_dir=args.output_dir)
