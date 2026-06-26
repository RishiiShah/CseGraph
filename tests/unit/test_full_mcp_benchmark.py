import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from run_full_mcp_benchmark import pick_graph_target


def test_pick_graph_target_rejects_display_label_and_uses_symbol() -> None:
    payload = {
        "target": "inferred from task",
        "symbols": [{"id": "symbol::app/orders.py::function::process_order"}],
    }

    assert pick_graph_target(payload) == "symbol::app/orders.py::function::process_order"


def test_pick_graph_target_accepts_structured_target_id() -> None:
    payload = {
        "target": {
            "id": "symbol::app/orders.py::function::process_order",
            "resolution": "inferred",
        }
    }

    assert pick_graph_target(payload) == "symbol::app/orders.py::function::process_order"


def test_pick_graph_target_prefers_graph_target_id() -> None:
    payload = {
        "target": {
            "id": "Order processing",
            "graph_target_id": "symbol::app/orders.py::function::process_order",
        },
        "symbols": [{"id": "symbol::app/orders.py::function::fallback"}],
    }

    assert pick_graph_target(payload) == "symbol::app/orders.py::function::process_order"
