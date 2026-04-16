"""Unit tests for the graph_analytics sandbox.

The SANDBOX_PATH env var lets the runner redirect imports to a temp copy
of the sandbox where a generated file has been substituted in.
"""
import os
import sys

SANDBOX_PATH = os.environ.get(
    "SANDBOX_PATH",
    os.path.join(os.path.dirname(__file__), "..", "fixtures", "sandboxes", "graph_analytics"),
)
sys.path.insert(0, os.path.abspath(SANDBOX_PATH))

from graph.search import bfs
from graph.shortest_path import shortest_path_length
from metrics.evaluator import GraphQueryEvaluator, evaluate_query


# ---------------------------------------------------------------------------
# bfs
# ---------------------------------------------------------------------------

def test_bfs_returns_zero_for_same_node():
    g = {"A": ["B"], "B": []}
    assert bfs(g, "A", "A") == 0


def test_bfs_direct_neighbor():
    g = {"A": ["B"], "B": []}
    assert bfs(g, "A", "B") == 1


def test_bfs_two_hops():
    g = {"A": ["B"], "B": ["C"], "C": []}
    assert bfs(g, "A", "C") == 2


def test_bfs_shortest_among_alternatives():
    # Both "A→B→D" and "A→C→D" have length 2; either is fine.
    g = {"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []}
    assert bfs(g, "A", "D") == 2


def test_bfs_no_path_returns_minus_one():
    g = {"A": ["B"], "B": [], "C": []}
    assert bfs(g, "A", "C") == -1


def test_bfs_missing_start_key():
    g = {"B": []}
    # Start node not in graph — should not reach "B" normally; returns -1 or 0
    result = bfs(g, "A", "B")
    assert result == -1


# ---------------------------------------------------------------------------
# shortest_path_length
# ---------------------------------------------------------------------------

def test_shortest_path_length_delegates_to_bfs():
    g = {"X": ["Y"], "Y": ["Z"], "Z": []}
    assert shortest_path_length(g, "X", "Z") == 2


def test_shortest_path_length_no_path():
    g = {"X": [], "Z": []}
    assert shortest_path_length(g, "X", "Z") == -1


# ---------------------------------------------------------------------------
# evaluate_query / GraphQueryEvaluator
# ---------------------------------------------------------------------------

def test_evaluate_query_returns_required_keys():
    g = {"A": ["B"], "B": []}
    result = evaluate_query(g, "A", "B")
    assert "src" in result
    assert "dst" in result
    assert "shortest_path" in result
    assert "reachable_nodes" in result


def test_evaluate_query_correct_src_dst():
    g = {"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []}
    result = evaluate_query(g, "A", "D")
    assert result["src"] == "A"
    assert result["dst"] == "D"


def test_evaluate_query_correct_shortest_path():
    g = {"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []}
    result = evaluate_query(g, "A", "D")
    assert result["shortest_path"] == 2


def test_evaluate_query_reachable_nodes_count():
    g = {"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []}
    result = evaluate_query(g, "A", "D")
    # A can reach A, B, C, D → 4 nodes (including itself)
    assert result["reachable_nodes"] == 4


def test_evaluate_query_unreachable_dst():
    g = {"A": ["B"], "B": [], "C": []}
    result = evaluate_query(g, "A", "C")
    assert result["shortest_path"] == -1


def test_graph_query_evaluator_uses_engine():
    evaluator = GraphQueryEvaluator()
    g = {"P": ["Q"], "Q": []}
    result = evaluator.evaluate(g, "P", "Q")
    assert result["shortest_path"] == 1
