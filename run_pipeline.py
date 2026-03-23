import argparse
import os

from agents.ingestion_agent import IngestionAgent
from agents.linking_agent import LinkingAgent


DEFAULT_SCAN_ROOT = "tests/fixtures/sandboxes/baseline_import_resolution"


def run_pipeline(root_dir: str, output_dir: str) -> None:
    abs_root = os.path.abspath(root_dir)
    abs_output_dir = os.path.abspath(output_dir)
    os.makedirs(abs_output_dir, exist_ok=True)

    ingestion_agent = IngestionAgent(abs_root)
    parsed_files = ingestion_agent.ingest_repository()
    ingested_output = os.path.join(abs_output_dir, "ingested_data.json")
    ingestion_agent.save_to_json(parsed_files, ingested_output)

    linking_agent = LinkingAgent(abs_root)
    graph = linking_agent.build_graph()
    graph_output = os.path.join(abs_output_dir, "link_graph.json")
    linking_agent.save_graph(graph, graph_output)

    print(f"Ingestion complete: {len(parsed_files)} files -> '{ingested_output}'")
    print(
        "Linking complete: "
        f"{graph.summary.file_count} files, "
        f"{graph.summary.symbol_count} symbols, "
        f"{graph.summary.edge_count} edges -> '{graph_output}'"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ingestion and linking stages for the repository."
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
