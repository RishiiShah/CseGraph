from csegraph._core.retrieval.context import _initial_detail_level
from csegraph._core.retrieval.scoring import lexical_scores
from csegraph._core.retrieval.target_resolution import TargetResolution, resolve_target


def _symbol(
    name: str,
    path: str,
    *,
    kind: str = "function",
    node_id: str | None = None,
) -> dict[str, object]:
    return {
        "id": node_id or f"symbol::{path}::{kind}::{name}",
        "name": name,
        "path": path,
        "file_path": path,
        "type": kind,
        "kind": kind,
        "signature": f"def {name}()",
        "docstring": "",
        "language": "python",
    }


def test_debug_target_tie_requires_confirmation():
    symbols = {
        "one": _symbol("handle_request", "api.py", node_id="one"),
        "two": _symbol("handle_request", "worker.py", node_id="two"),
    }

    resolution = resolve_target(
        None,
        "Fix the failing handle_request error",
        symbols,
        summaries={},
    )

    assert resolution.status == "ambiguous"
    assert resolution.target_id == ""
    assert len(resolution.candidates) == 2


def test_stack_trace_file_outranks_similarly_named_test():
    symbols = {
        "production": _symbol("process_order", "app/orders.py"),
        "test": _symbol("test_process_order", "tests/test_orders.py", kind="test"),
    }
    task = (
        "Fix this traceback: File \"/workspace/app/orders.py\", line 42, "
        "in process_order RuntimeError"
    )

    scores, evidence = lexical_scores(task, symbols, summaries={})

    assert scores["production"] > scores["test"]
    assert "bug-file-evidence" in evidence["production"]
    assert "bug-stack-symbol" in evidence["production"]


def test_generic_failure_word_does_not_promote_tests():
    symbols = {
        "production": _symbol("process_order", "app/orders.py"),
        "test": _symbol("test_process_order", "tests/test_orders.py", kind="test"),
    }

    scores, _ = lexical_scores("fix failing process_order", symbols, summaries={})

    assert scores["production"] > scores["test"]


def test_trusted_debug_target_gets_working_context_in_one_call():
    resolution = TargetResolution(
        status="resolved",
        target_id="production",
        confidence=1.0,
    )

    assert _initial_detail_level("auto", "Fix the process_order crash", resolution) == "standard"
    assert _initial_detail_level("auto", "Explain process_order", resolution) == "minimal"
