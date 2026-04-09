"""run_pipeline.py

Full pipeline: Ingestion -> Linking -> Compression -> CSE -> CodeGen

Usage examples
--------------
# Run on every sandbox (one .py generated per sandbox):
    python run_pipeline.py --all-sandboxes

# Run on a single sandbox:
    python run_pipeline.py --root-dir tests/fixtures/sandboxes/graph_analytics

# Run without code generation (CSE only):
    python run_pipeline.py --all-sandboxes --skip-codegen

# Run a single agent standalone:
    python agents/ingestion_agent.py  --root-dir <path>
    python agents/linking_agent.py    --root-dir <path>
    python agents/compression_agent.py --graph-path data/link_graph.json
    python agents/cse_agent.py        --link-graph data/link_graph.json --compressed-graph data/compressed_graph.json
    python agents/code_gen_agent.py   --link-graph data/link_graph.json --compressed-graph data/compressed_graph.json --cse-result data/cse_result.json
"""

import argparse
import os

from dotenv import load_dotenv
load_dotenv()

from agents.ingestion_agent import IngestionAgent
from agents.linking_agent import LinkingAgent
from agents.compression_agent import CompressionAgent
from agents.cse_agent import CSEAgent
from agents.code_gen_agent import CodeGenAgent
from models.cse_result import SufficiencyQuery


DEFAULT_SANDBOX_ROOT = "tests/fixtures/sandboxes"


# ---------------------------------------------------------------------------
# Single-repo pipeline
# ---------------------------------------------------------------------------

def run_pipeline(root_dir: str, output_dir: str, skip_codegen: bool = False) -> None:
    """Run the full 5-step pipeline on one repository root."""
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
    target_id, target_file, auto_query = cse.pick_representative_target()
    sample_query = SufficiencyQuery(
        query_text=auto_query,
        target_node_id=target_id,
        target_file_path=target_file,
    )
    cse_result = cse.evaluate(sample_query)
    cse_output = os.path.join(abs_output_dir, "cse_result.json")
    cse.save_result(cse_result, cse_output)

    print(f"Ingestion complete   : {len(parsed_files)} files -> '{ingested_output}'")
    print(
        f"Linking complete     : "
        f"{graph.summary.file_count} files, "
        f"{graph.summary.symbol_count} symbols, "
        f"{graph.summary.edge_count} edges -> '{graph_output}'"
    )
    print(
        f"Compression complete : "
        f"{len(compressed.node_summaries)} summaries, "
        f"{len(compressed.context_slices)} context slices -> '{compressed_output}'"
    )
    print(
        f"CSE complete         : sufficient={cse_result.is_sufficient}, "
        f"rounds={cse_result.expansion_rounds}/{cse_result.max_rounds}, "
        f"dep={cse_result.metrics.dependency_completeness:.0%}, "
        f"ent={cse_result.metrics.entity_coverage:.0%}, "
        f"sem={cse_result.metrics.semantic_overlap:.0%}, "
        f"conf={cse_result.metrics.model_confidence:.0%}, "
        f"raw_code_nodes={len(cse_result.raw_code_nodes)} "
        f"-> '{cse_output}'"
    )

    # Step 5: Code Generation — gated on CSE
    if skip_codegen:
        print("CodeGen              : skipped (--skip-codegen)")
        return

    if not cse_result.is_sufficient:
        print("CodeGen              : skipped — CSE context not sufficient")
        return

    try:
        code_gen = CodeGenAgent(graph_output, compressed_output)
    except (ImportError, ValueError) as e:
        print(f"CodeGen              : skipped — {e}")
        return

    code_gen_result = code_gen.generate(cse_result)

    code_gen_json = os.path.join(abs_output_dir, "code_gen_result.json")
    code_gen.save_result(code_gen_result, code_gen_json)

    target_stem = target_id.split("::")[-1].replace(".", "_").replace(" ", "_")
    code_gen_py = os.path.join(abs_output_dir, f"generated_{target_stem}.py")
    code_gen.save_code(code_gen_result, code_gen_py)

    print(
        f"CodeGen complete     : model={code_gen_result.model}, "
        f"context_nodes={len(code_gen_result.context_nodes_used)}, "
        f"raw_code_nodes={len(code_gen_result.raw_code_nodes_used)}, "
        f"tokens={code_gen_result.prompt_tokens}+{code_gen_result.completion_tokens} "
        f"-> '{code_gen_py}'"
    )


# ---------------------------------------------------------------------------
# All-sandboxes mode
# ---------------------------------------------------------------------------

def run_all_sandboxes(
    sandboxes_root: str, output_dir: str, skip_codegen: bool = False
) -> None:
    """Run the full pipeline on every subdirectory inside sandboxes_root."""
    abs_root = os.path.abspath(sandboxes_root)
    sandboxes = sorted(
        name for name in os.listdir(abs_root)
        if os.path.isdir(os.path.join(abs_root, name))
    )

    if not sandboxes:
        print(f"No sandbox directories found in '{abs_root}'")
        return

    print(f"Found {len(sandboxes)} sandboxes: {', '.join(sandboxes)}\n")

    for sandbox in sandboxes:
        sandbox_path = os.path.join(abs_root, sandbox)
        sandbox_output = os.path.join(os.path.abspath(output_dir), sandbox)
        print(f"{'='*60}")
        print(f"Sandbox: {sandbox}")
        print(f"{'='*60}")
        try:
            run_pipeline(sandbox_path, sandbox_output, skip_codegen=skip_codegen)
        except Exception as e:
            print(f"ERROR in '{sandbox}': {e}")
        print()

    # Summary of generated files
    print(f"{'='*60}")
    print("Generated files:")
    for sandbox in sandboxes:
        sandbox_output = os.path.join(os.path.abspath(output_dir), sandbox)
        py_files = [
            f for f in os.listdir(sandbox_output)
            if f.startswith("generated_") and f.endswith(".py")
        ] if os.path.isdir(sandbox_output) else []
        status = py_files[0] if py_files else "— no .py generated"
        print(f"  {sandbox:<35} {status}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the full pipeline: "
            "Ingestion -> Linking -> Compression -> CSE -> CodeGen."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--root-dir",
        default=None,
        help="Single repository root to analyze.",
    )
    parser.add_argument(
        "--all-sandboxes",
        action="store_true",
        help=(
            f"Run on every subdirectory inside --sandbox-root "
            f"(default root: {DEFAULT_SANDBOX_ROOT})."
        ),
    )
    parser.add_argument(
        "--sandbox-root",
        default=DEFAULT_SANDBOX_ROOT,
        help=f"Parent directory containing sandbox repos (default: {DEFAULT_SANDBOX_ROOT}).",
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where pipeline artifacts are written (default: data).",
    )
    parser.add_argument(
        "--skip-codegen",
        action="store_true",
        help="Skip the Code Generation step.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.all_sandboxes:
        run_all_sandboxes(
            sandboxes_root=args.sandbox_root,
            output_dir=args.output_dir,
            skip_codegen=args.skip_codegen,
        )
    elif args.root_dir:
        run_pipeline(
            root_dir=args.root_dir,
            output_dir=args.output_dir,
            skip_codegen=args.skip_codegen,
        )
    else:
        # Default: run on all sandboxes
        run_all_sandboxes(
            sandboxes_root=args.sandbox_root,
            output_dir=args.output_dir,
            skip_codegen=args.skip_codegen,
        )
