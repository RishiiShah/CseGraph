"""compare_baselines.py — Side-by-side comparison of three context-selection strategies.

For each sandbox in sandboxes/ the script:
  1. Runs IngestionAgent → LinkingAgent → CompressionAgent (shared pipeline).
  2. Picks a representative target via CSEAgent.pick_representative_target().
  3. Runs all three agents with the same SufficiencyQuery:
       - adaptive     : CSEAgent.evaluate(query)
       - full_context : FullContextAgent.build_context(query)
       - static_rag   : StaticRAGAgent.build_context(query)
  4. Optionally runs CodeGenAgent.generate(result) for each strategy.
  5. Checks compilability of the generated code via ast.parse().
  6. Prints a compact per-sandbox table and saves aggregate outputs.

Outputs (saved to --output-dir, default: data/):
  baseline_comparison.json   — full per-sandbox×strategy results
  baseline_comparison.csv    — flat table (one row per sandbox×strategy)
  baseline_summary.json      — mean per strategy across all sandboxes
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Agent / model imports
# ---------------------------------------------------------------------------
from agents.ingestion_agent import IngestionAgent
from agents.linking_agent import LinkingAgent
from agents.compression_agent import CompressionAgent
from agents.cse_agent import CSEAgent
from agents.full_context_agent import FullContextAgent
from agents.static_rag_agent import StaticRAGAgent
from models.cse_result import SufficiencyQuery, SufficiencyResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(obj: Any, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=4)


LOGPROB_THRESHOLD: float = -2.0  # mean log-prob below this triggers re-expansion

_SANDBOX_UNIT_TEST_DIR = os.path.join(
    os.path.dirname(__file__), "tests", "sandbox_unit_tests"
)

# Maps sandbox directory name → unit-test file (relative to _SANDBOX_UNIT_TEST_DIR)
_SANDBOX_TEST_FILES: Dict[str, str] = {
    "graph_analytics": "test_graph_analytics.py",
    "etl_pipeline_oop": "test_etl_pipeline_oop.py",
    "user_service_api": "test_user_service_api.py",
    "event_driven_orders": "test_event_driven_orders.py",
    "nlp_chunking_pipeline": "test_nlp_chunking_pipeline.py",
    "baseline_import_resolution": "test_baseline_import_resolution.py",
    "ml_training_pipeline": "test_ml_training_pipeline.py",
}


def _parse_pytest_output(output: str) -> Dict[str, Any]:
    """Extract passed/failed/error counts from pytest -q terminal output."""
    passed = failed = errors = 0
    # e.g. "14 passed, 1 failed in 0.12s"  or  "14 passed in 0.07s"
    for m in re.finditer(r"(\d+)\s+(passed|failed|error)", output):
        count, label = int(m.group(1)), m.group(2)
        if label == "passed":
            passed = count
        elif label == "failed":
            failed = count
        elif label == "error":
            errors = count
    total = passed + failed + errors
    return {
        "unit_tests_run": total,
        "unit_tests_passed": passed,
        "unit_tests_failed": failed + errors,
        "unit_test_pass_rate": round(passed / total, 4) if total else None,
    }


def _run_sandbox_tests(
    sandbox_name: str,
    sandbox_path: str,
    generated_code: Optional[str] = None,
    target_file_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the unit-test suite for *sandbox_name*.

    If *generated_code* is provided the target file inside a temporary copy of
    the sandbox is replaced before running, so the tests exercise the generated
    code rather than the original.  The env-var ``SANDBOX_PATH`` tells each
    test module where to import from.

    Returns a dict with keys:
        unit_tests_run, unit_tests_passed, unit_tests_failed,
        unit_test_pass_rate, unit_test_error
    """
    empty: Dict[str, Any] = {
        "unit_tests_run": None,
        "unit_tests_passed": None,
        "unit_tests_failed": None,
        "unit_test_pass_rate": None,
        "unit_test_error": None,
    }

    test_filename = _SANDBOX_TEST_FILES.get(sandbox_name)
    if not test_filename:
        empty["unit_test_error"] = f"no test file registered for '{sandbox_name}'"
        return empty

    test_file = os.path.join(_SANDBOX_UNIT_TEST_DIR, test_filename)
    if not os.path.exists(test_file):
        empty["unit_test_error"] = f"test file not found: {test_file}"
        return empty

    # Decide which sandbox path tests should import from.
    if generated_code and target_file_path:
        tmpdir = tempfile.mkdtemp(prefix="sandbox_gen_")
        try:
            temp_sandbox = os.path.join(tmpdir, sandbox_name)
            shutil.copytree(sandbox_path, temp_sandbox)
            target_abs = os.path.join(temp_sandbox, target_file_path)
            os.makedirs(os.path.dirname(target_abs), exist_ok=True)
            with open(target_abs, "w", encoding="utf-8") as fh:
                fh.write(generated_code)
            effective_sandbox = temp_sandbox
            return _exec_pytest(test_file, effective_sandbox, empty, tmpdir)
        except Exception as exc:
            empty["unit_test_error"] = f"sandbox copy failed: {exc}"
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass
            return empty
    else:
        return _exec_pytest(test_file, sandbox_path, empty, cleanup_dir=None)


def _exec_pytest(
    test_file: str,
    sandbox_path: str,
    empty: Dict[str, Any],
    cleanup_dir: Optional[str],
) -> Dict[str, Any]:
    env = {**os.environ, "SANDBOX_PATH": os.path.abspath(sandbox_path)}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", test_file, "--tb=no", "-q", "--no-header"],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        output = proc.stdout + proc.stderr
        result = _parse_pytest_output(output)
        result["unit_test_error"] = None
        if result["unit_tests_run"] == 0:
            result["unit_test_error"] = output.strip()[:300] or "no tests collected"
        return result
    except subprocess.TimeoutExpired:
        empty["unit_test_error"] = "pytest timed out (60 s)"
        return empty
    except Exception as exc:
        empty["unit_test_error"] = str(exc)
        return empty
    finally:
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)


def _run_codegen(
    link_graph_path: str,
    compressed_graph_path: str,
    result: SufficiencyResult,
) -> Dict[str, Any]:
    """Run CodeGenAgent and return a dict with codegen fields.

    Returns dict with keys:
        generated_code, prompt_tokens, completion_tokens, total_tokens,
        compiled, compile_error
    All fields are None if codegen is unavailable (missing key / import error).
    """
    empty: Dict[str, Any] = {
        "generated_code": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "compiled": None,
        "compile_error": None,
        "mean_logprob": None,
        "generated_test_code": None,
        "test_code_compiled": None,
    }
    try:
        from agents.code_gen_agent import CodeGenAgent
        agent = CodeGenAgent(link_graph_path, compressed_graph_path)
    except (ImportError, ValueError) as exc:
        empty["compile_error"] = f"CodeGenAgent unavailable: {exc}"
        return empty

    try:
        gen = agent.generate(result)
    except Exception as exc:
        empty["compile_error"] = f"CodeGenAgent.generate() failed: {exc}"
        return empty

    prompt_tok = gen.prompt_tokens
    completion_tok = gen.completion_tokens
    total_tok = (
        (prompt_tok or 0) + (completion_tok or 0)
        if (prompt_tok is not None or completion_tok is not None)
        else None
    )

    compiled: Optional[bool] = None
    compile_error: Optional[str] = None
    if gen.generated_code:
        try:
            ast.parse(gen.generated_code)
            compiled = True
        except SyntaxError as exc:
            compiled = False
            compile_error = str(exc)

    # --- Test generation: find related test file, then generate/update tests ---
    generated_test_code: Optional[str] = None
    test_code_compiled: Optional[bool] = None
    try:
        test_file_path = CodeGenAgent.find_test_file(
            result.query.target_file_path,
            agent._link_graph.root_dir,
        )
        existing_test: Optional[str] = None
        if test_file_path:
            with open(test_file_path, "r", encoding="utf-8") as tf:
                existing_test = tf.read()
            print(f"  [test-gen] updating existing test file: {test_file_path}")
        else:
            print(f"  [test-gen] no existing test file found; generating from scratch")
        generated_test_code = agent.generate_tests(gen, existing_test_content=existing_test)
        if generated_test_code:
            try:
                ast.parse(generated_test_code)
                test_code_compiled = True
            except SyntaxError:
                test_code_compiled = False
    except Exception as exc:
        print(f"  [test-gen] error: {exc}")

    return {
        "generated_code": gen.generated_code,
        "prompt_tokens": prompt_tok,
        "completion_tokens": completion_tok,
        "total_tokens": total_tok,
        "compiled": compiled,
        "compile_error": compile_error,
        "mean_logprob": gen.mean_logprob,
        "generated_test_code": generated_test_code,
        "test_code_compiled": test_code_compiled,
    }


def _result_to_dict(result: SufficiencyResult) -> Dict[str, Any]:
    """Extract the fields we care about from a SufficiencyResult."""
    context_node_count = len(result.context_node_ids) + len(result.raw_code_nodes)
    return {
        "context_node_count": context_node_count,
        "raw_code_nodes_count": len(result.raw_code_nodes),
        "is_sufficient": result.is_sufficient,
        "expansion_rounds": result.expansion_rounds,
        "recompressed_rounds": result.recompressed_rounds,
        "dep_completeness": result.metrics.dependency_completeness,
        "entity_coverage": result.metrics.entity_coverage,
        "semantic_overlap": result.metrics.semantic_overlap,
        "model_confidence": result.metrics.model_confidence,
        "reason": result.reason,
    }


def _print_table(sandbox_name: str, strategies: Dict[str, Dict[str, Any]]) -> None:
    """Print a compact per-sandbox comparison table."""
    header = (
        f"{'strategy':<14} {'nodes':>5} {'raw':>4} {'prompt_tok':>10} {'total_tok':>9} "
        f"{'dep%':>6} {'ent%':>6} {'conf%':>6} {'compiled':>8} {'unit%':>7} {'mean_lp':>8}"
    )
    sep = "-" * len(header)
    print(f"\n=== {sandbox_name} ===")
    print(header)
    print(sep)
    for strategy, row in strategies.items():
        compiled_str = (
            "yes" if row.get("compiled") is True
            else "no" if row.get("compiled") is False
            else "skip"
        )
        unit_rate = row.get("unit_test_pass_rate")
        unit_str = f"{unit_rate:.1%}" if unit_rate is not None else "-"
        lp = row.get("mean_logprob")
        lp_str = f"{lp:.3f}" if lp is not None else "-"
        print(
            f"{strategy:<14} "
            f"{row['context_node_count']:>5} "
            f"{row.get('raw_code_nodes_count', 0):>4} "
            f"{str(row.get('prompt_tokens') or '-'):>10} "
            f"{str(row.get('total_tokens') or '-'):>9} "
            f"{row['dep_completeness']:>6.1%} "
            f"{row['entity_coverage']:>6.1%} "
            f"{row['model_confidence']:>6.1%} "
            f"{compiled_str:>8} "
            f"{unit_str:>7} "
            f"{lp_str:>8}"
        )


# ---------------------------------------------------------------------------
# Core benchmark runner
# ---------------------------------------------------------------------------

def run_comparison(
    sandbox_root: str,
    output_dir: str,
    top_k: int = 20,
    skip_codegen: bool = False,
    num_targets: int = 3,
) -> None:
    abs_root = os.path.abspath(sandbox_root)
    sandbox_names = sorted(
        name
        for name in os.listdir(abs_root)
        if os.path.isdir(os.path.join(abs_root, name))
    )

    all_results: List[Dict[str, Any]] = []

    for sandbox_name in sandbox_names:
        sandbox_path = os.path.join(abs_root, sandbox_name)
        print(f"\n[{sandbox_name}] Running shared pipeline …")

        # ------------------------------------------------------------------
        # 1. Shared pipeline: Ingestion → Linking → Compression
        # ------------------------------------------------------------------
        ingestion_agent = IngestionAgent(sandbox_path)
        ingestion_agent.ingest_repository()

        linking_agent = LinkingAgent(sandbox_path)
        graph = linking_agent.build_graph()

        with tempfile.TemporaryDirectory(prefix="compare_") as tmp_dir:
            link_graph_path = os.path.join(tmp_dir, "link_graph.json")
            linking_agent.save_graph(graph, link_graph_path)

            compression_agent = CompressionAgent(link_graph_path)
            compressed = compression_agent.compress()

            compressed_graph_path = os.path.join(tmp_dir, "compressed_graph.json")
            compression_agent.save_compressed(compressed, compressed_graph_path)

            # ------------------------------------------------------------------
            # 2. Pick multiple representative targets
            # ------------------------------------------------------------------
            cse = CSEAgent(
                link_graph_path,
                compressed_graph_path,
                resummary_fn=compression_agent._generate_node_summary,
            )
            targets = cse.pick_top_n_targets(n=num_targets)
            node_summary_count = len(compressed.node_summaries)

            full_agent = FullContextAgent(link_graph_path, compressed_graph_path)
            rag_agent = StaticRAGAgent(link_graph_path, compressed_graph_path, top_k=top_k)

            for target_idx, (target_id, target_file, auto_query) in enumerate(targets):
                print(f"  target [{target_idx + 1}/{len(targets)}]: {target_id}")
                print(f"  query : {auto_query[:80]}…" if len(auto_query) > 80 else f"  query : {auto_query}")

                query = SufficiencyQuery(
                    query_text=auto_query,
                    target_node_id=target_id,
                    target_file_path=target_file,
                )

                # --------------------------------------------------------------
                # 3. Run all three strategies
                # --------------------------------------------------------------

                # --- adaptive ---
                print("  [adaptive] running CSEAgent.evaluate() …")
                adaptive_result = cse.evaluate(query)
                adaptive_row = _result_to_dict(adaptive_result)

                # --- full_context ---
                print("  [full_context] running FullContextAgent.build_context() …")
                full_result = full_agent.build_context(query)
                full_row = _result_to_dict(full_result)

                # --- static_rag ---
                print(f"  [static_rag] running StaticRAGAgent.build_context(top_k={top_k}) …")
                rag_result = rag_agent.build_context(query)
                rag_row = _result_to_dict(rag_result)

                # --------------------------------------------------------------
                # 4. CodeGen + unit tests (optional)
                # --------------------------------------------------------------
                _codegen_empty = {
                    "generated_code": None,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_tokens": None,
                    "compiled": None,
                    "compile_error": None,
                    "mean_logprob": None,
                    "generated_test_code": None,
                    "test_code_compiled": None,
                }
                _unit_test_empty = {
                    "unit_tests_run": None,
                    "unit_tests_passed": None,
                    "unit_tests_failed": None,
                    "unit_test_pass_rate": None,
                    "unit_test_error": None,
                }
                for label, result, row in [
                    ("adaptive", adaptive_result, adaptive_row),
                    ("full_context", full_result, full_row),
                    ("static_rag", rag_result, rag_row),
                ]:
                    if skip_codegen:
                        row.update(_codegen_empty)
                        row.update(_unit_test_empty)
                        row["logprob_triggered_regen"] = False
                    else:
                        print(f"  [{label}] running CodeGenAgent.generate() …")
                        codegen_data = _run_codegen(
                            link_graph_path, compressed_graph_path, result
                        )
                        row.update(codegen_data)

                        # --------------------------------------------------
                        # Logprob feedback loop (adaptive only)
                        # If mean_logprob is below LOGPROB_THRESHOLD the model
                        # was not confident; expand context one more step and
                        # regenerate (proposal §3.3 log-probability feedback).
                        # --------------------------------------------------
                        logprob_triggered = False
                        if label == "adaptive":
                            mean_lp = codegen_data.get("mean_logprob")
                            if mean_lp is not None and mean_lp < LOGPROB_THRESHOLD:
                                print(
                                    f"  [adaptive] mean_logprob={mean_lp:.3f} < "
                                    f"{LOGPROB_THRESHOLD}; re-expanding context …"
                                )
                                expanded_result = cse.expand_for_query(
                                    query=query,
                                    context_ids=list(result.context_node_ids),
                                    raw_code_ids=list(result.raw_code_nodes),
                                    reason_prefix=(
                                        f"Logprob-triggered re-expansion "
                                        f"(mean_logprob={mean_lp:.3f})"
                                    ),
                                )
                                print(
                                    f"  [adaptive] re-generating after expansion "
                                    f"({len(expanded_result.context_node_ids)} nodes) …"
                                )
                                codegen_data = _run_codegen(
                                    link_graph_path, compressed_graph_path, expanded_result
                                )
                                row.update(_result_to_dict(expanded_result))
                                row.update(codegen_data)
                                logprob_triggered = True
                        row["logprob_triggered_regen"] = logprob_triggered

                        # Run sandbox unit tests against the generated code
                        print(f"  [{label}] running sandbox unit tests …")
                        unit_test_data = _run_sandbox_tests(
                            sandbox_name=sandbox_name,
                            sandbox_path=sandbox_path,
                            generated_code=codegen_data.get("generated_code"),
                            target_file_path=target_file,
                        )
                        row.update(unit_test_data)

                # --------------------------------------------------------------
                # 5. Print compact table
                # --------------------------------------------------------------
                _print_table(
                    f"{sandbox_name} / target {target_idx + 1}",
                    {
                        "adaptive": adaptive_row,
                        "full_context": full_row,
                        "static_rag": rag_row,
                    },
                )

                # --------------------------------------------------------------
                # 6. Collect full results
                # --------------------------------------------------------------
                all_results.append({
                    "sandbox": sandbox_name,
                    "sandbox_path": sandbox_path,
                    "target_index": target_idx,
                    "target_node_id": target_id,
                    "target_file_path": target_file,
                    "query_text": auto_query,
                    "node_summary_count": node_summary_count,
                    "strategies": {
                        "adaptive": adaptive_row,
                        "full_context": full_row,
                        "static_rag": rag_row,
                    },
                })

    # --------------------------------------------------------------------------
    # Save outputs
    # --------------------------------------------------------------------------
    os.makedirs(output_dir, exist_ok=True)

    # --- baseline_comparison.json ---
    json_path = os.path.join(output_dir, "baseline_comparison.json")
    _write_json(all_results, json_path)
    print(f"\nSaved full results to '{json_path}'")

    # --- baseline_comparison.csv ---
    csv_columns = [
        "sandbox",
        "target_index",
        "strategy",
        "context_node_count",
        "raw_code_nodes_count",
        "is_sufficient",
        "expansion_rounds",
        "recompressed_rounds",
        "dep_completeness",
        "entity_coverage",
        "semantic_overlap",
        "model_confidence",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "compiled",
        "compile_error",
        "mean_logprob",
        "logprob_triggered_regen",
        "test_code_compiled",
        "unit_tests_run",
        "unit_tests_passed",
        "unit_tests_failed",
        "unit_test_pass_rate",
        "unit_test_error",
    ]
    csv_path = os.path.join(output_dir, "baseline_comparison.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_columns)
        writer.writeheader()
        for entry in all_results:
            for strategy, row in entry["strategies"].items():
                writer.writerow({
                    "sandbox": entry["sandbox"],
                    "target_index": entry.get("target_index", 0),
                    "strategy": strategy,
                    "context_node_count": row["context_node_count"],
                    "raw_code_nodes_count": row.get("raw_code_nodes_count", 0),
                    "is_sufficient": row["is_sufficient"],
                    "expansion_rounds": row["expansion_rounds"],
                    "recompressed_rounds": row.get("recompressed_rounds", 0),
                    "dep_completeness": row["dep_completeness"],
                    "entity_coverage": row["entity_coverage"],
                    "semantic_overlap": row["semantic_overlap"],
                    "model_confidence": row["model_confidence"],
                    "prompt_tokens": row.get("prompt_tokens"),
                    "completion_tokens": row.get("completion_tokens"),
                    "total_tokens": row.get("total_tokens"),
                    "compiled": row.get("compiled"),
                    "compile_error": row.get("compile_error"),
                    "mean_logprob": row.get("mean_logprob"),
                    "logprob_triggered_regen": row.get("logprob_triggered_regen", False),
                    "test_code_compiled": row.get("test_code_compiled"),
                    "unit_tests_run": row.get("unit_tests_run"),
                    "unit_tests_passed": row.get("unit_tests_passed"),
                    "unit_tests_failed": row.get("unit_tests_failed"),
                    "unit_test_pass_rate": row.get("unit_test_pass_rate"),
                    "unit_test_error": row.get("unit_test_error"),
                })
    print(f"Saved CSV to '{csv_path}'")

    # --- baseline_summary.json ---
    summary = _compute_summary(all_results)
    summary_path = os.path.join(output_dir, "baseline_summary.json")
    _write_json(summary, summary_path)
    print(f"Saved summary to '{summary_path}'")

    # Print summary table
    _print_summary(summary)


# ---------------------------------------------------------------------------
# Summary computation
# ---------------------------------------------------------------------------

_NUMERIC_FIELDS = [
    "context_node_count",
    "raw_code_nodes_count",
    "expansion_rounds",
    "recompressed_rounds",
    "dep_completeness",
    "entity_coverage",
    "semantic_overlap",
    "model_confidence",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "mean_logprob",
    "unit_test_pass_rate",
]

_STRATEGIES = ("adaptive", "full_context", "static_rag")


def _safe_mean(values: List[Any]) -> Optional[float]:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _compile_success_rate(compiled_list: List[Any]) -> Optional[float]:
    non_none = [v for v in compiled_list if v is not None]
    if not non_none:
        return None
    return sum(1 for v in non_none if v is True) / len(non_none)


def _accum_empty() -> Dict[str, List[Any]]:
    return {f: [] for f in _NUMERIC_FIELDS + ["compiled"]}


def _accum_to_means(accum: Dict[str, List[Any]]) -> Dict[str, Any]:
    means: Dict[str, Any] = {f: _safe_mean(accum[f]) for f in _NUMERIC_FIELDS}
    means["compile_success_rate"] = _compile_success_rate(accum["compiled"])
    return means


def _compute_summary(all_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute mean per strategy — globally and per sandbox.

    Returns
    -------
    {
        "global":     { strategy: { field: mean, ... }, ... },
        "per_sandbox":{ sandbox:  { strategy: { field: mean, ... }, ... }, ... },
    }
    """
    global_accum: Dict[str, Dict[str, List[Any]]] = {
        s: _accum_empty() for s in _STRATEGIES
    }
    per_sandbox_accum: Dict[str, Dict[str, Dict[str, List[Any]]]] = {}

    for entry in all_results:
        sb = entry["sandbox"]
        if sb not in per_sandbox_accum:
            per_sandbox_accum[sb] = {s: _accum_empty() for s in _STRATEGIES}

        for strategy, row in entry["strategies"].items():
            if strategy not in global_accum:
                continue
            for field in _NUMERIC_FIELDS:
                global_accum[strategy][field].append(row.get(field))
                per_sandbox_accum[sb][strategy][field].append(row.get(field))
            global_accum[strategy]["compiled"].append(row.get("compiled"))
            per_sandbox_accum[sb][strategy]["compiled"].append(row.get("compiled"))

    global_summary = {s: _accum_to_means(global_accum[s]) for s in _STRATEGIES}
    per_sandbox_summary = {
        sb: {s: _accum_to_means(per_sandbox_accum[sb][s]) for s in _STRATEGIES}
        for sb in per_sandbox_accum
    }

    return {"global": global_summary, "per_sandbox": per_sandbox_summary}


def _fmt(v: Any, pct: bool = False, decimals: int = 1) -> str:
    if v is None:
        return "-"
    if pct:
        return f"{v:.{decimals}%}"
    if isinstance(v, float):
        return f"{v:.{decimals}f}"
    return str(v)


def _print_strategy_rows(strategy_data: Dict[str, Any]) -> None:
    """Print one row per strategy using *strategy_data* = {strategy: {field: val}}."""
    for strategy in _STRATEGIES:
        m = strategy_data.get(strategy, {})
        print(
            f"  {strategy:<14} "
            f"{_fmt(m.get('context_node_count')):>6} "
            f"{_fmt(m.get('raw_code_nodes_count')):>5} "
            f"{_fmt(m.get('expansion_rounds'), decimals=1):>5} "
            f"{_fmt(m.get('dep_completeness'), pct=True):>6} "
            f"{_fmt(m.get('entity_coverage'), pct=True):>6} "
            f"{_fmt(m.get('model_confidence'), pct=True):>6} "
            f"{_fmt(m.get('prompt_tokens')):>8} "
            f"{_fmt(m.get('total_tokens')):>8} "
            f"{_fmt(m.get('compile_success_rate'), pct=True):>9} "
            f"{_fmt(m.get('unit_test_pass_rate'), pct=True):>7} "
            f"{_fmt(m.get('mean_logprob'), decimals=3):>8}"
        )


def _print_summary(summary: Dict[str, Any]) -> None:
    global_data = summary.get("global", summary)  # backward-compat if flat dict
    per_sandbox = summary.get("per_sandbox", {})

    col_header = (
        f"  {'strategy':<14} {'nodes':>6} {'raw':>5} {'exp':>5} {'dep%':>6} {'ent%':>6} "
        f"{'conf%':>6} {'ptok':>8} {'ttok':>8} {'compile%':>9} {'unit%':>7} {'mean_lp':>8}"
    )

    # --- Global means ---
    print("\n=== SUMMARY (means across all sandboxes) ===")
    print(col_header)
    print("  " + "-" * (len(col_header) - 2))
    _print_strategy_rows(global_data)

    # --- Per-sandbox breakdown ---
    if per_sandbox:
        print("\n=== PER-SANDBOX BREAKDOWN ===")
        for sandbox_name, strat_data in sorted(per_sandbox.items()):
            print(f"\n  [{sandbox_name}]")
            print(col_header)
            print("  " + "-" * (len(col_header) - 2))
            _print_strategy_rows(strat_data)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare adaptive / full-context / static-RAG context strategies across sandboxes."
    )
    parser.add_argument(
        "--sandbox-root",
        default="sandboxes",
        help="Directory containing sandbox repos (default: sandboxes).",
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory to write output files (default: data).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=20,
        help="Top-K for StaticRAGAgent (default: 20).",
    )
    parser.add_argument(
        "--num-targets",
        type=int,
        default=3,
        help="Number of targets to evaluate per sandbox (default: 3).",
    )
    parser.add_argument(
        "--skip-codegen",
        action="store_true",
        help="Skip LLM code generation calls entirely.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_comparison(
        sandbox_root=args.sandbox_root,
        output_dir=args.output_dir,
        top_k=args.top_k,
        skip_codegen=args.skip_codegen,
        num_targets=args.num_targets,
    )
