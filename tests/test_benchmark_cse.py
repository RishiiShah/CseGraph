import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest

from benchmark_sandboxes import run_benchmark

SANDBOX_ROOT = os.path.join(os.path.dirname(__file__), "..", "sandboxes")


# ---------------------------------------------------------------------------
# Guard: skip the whole module if the sandbox directory is missing.
# ---------------------------------------------------------------------------

if not os.path.isdir(SANDBOX_ROOT):
    pytest.skip(
        f"Sandbox fixture directory not found: {SANDBOX_ROOT}",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _results_with_cse():
    """Run benchmark once with include_cse=True and cache the return value."""
    return run_benchmark(SANDBOX_ROOT, include_cse=True)


def _results_without_cse():
    return run_benchmark(SANDBOX_ROOT, include_cse=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIncludeCseAddsFields:
    def test_at_least_one_result_has_node_summary_count(self):
        results = _results_with_cse()
        assert any(
            "node_summary_count" in r["metrics"] for r in results
        ), "Expected at least one result to contain 'node_summary_count'"

    def test_all_results_have_node_summary_count(self):
        results = _results_with_cse()
        assert results, "run_benchmark returned an empty list"
        for r in results:
            assert "node_summary_count" in r["metrics"], (
                f"Sandbox '{r['sandbox']}' is missing 'node_summary_count'"
            )

    def test_cse_context_nodes_key_present(self):
        results = _results_with_cse()
        for r in results:
            assert "cse_context_nodes" in r["metrics"], (
                f"Sandbox '{r['sandbox']}' is missing 'cse_context_nodes'"
            )


class TestIncludeCseFalseNoCseFields:
    def test_node_summary_count_absent(self):
        results = _results_without_cse()
        for r in results:
            assert "node_summary_count" not in r["metrics"], (
                f"Sandbox '{r['sandbox']}' unexpectedly contains 'node_summary_count'"
            )

    def test_cse_dep_completeness_absent(self):
        results = _results_without_cse()
        for r in results:
            assert "cse_dep_completeness" not in r["metrics"], (
                f"Sandbox '{r['sandbox']}' unexpectedly contains 'cse_dep_completeness'"
            )

    def test_cse_context_nodes_absent(self):
        results = _results_without_cse()
        for r in results:
            assert "cse_context_nodes" not in r["metrics"]


class TestCseMetricsInValidRange:
    def _cse_results(self):
        return [
            r for r in _results_with_cse()
            if r["metrics"].get("cse_dep_completeness") is not None
        ]

    def test_dep_completeness_in_range(self):
        for r in self._cse_results():
            val = r["metrics"]["cse_dep_completeness"]
            assert 0.0 <= val <= 1.0, (
                f"Sandbox '{r['sandbox']}': cse_dep_completeness={val} out of [0,1]"
            )

    def test_entity_coverage_in_range(self):
        for r in self._cse_results():
            val = r["metrics"]["cse_entity_coverage"]
            assert 0.0 <= val <= 1.0, (
                f"Sandbox '{r['sandbox']}': cse_entity_coverage={val} out of [0,1]"
            )

    def test_model_confidence_in_range(self):
        for r in self._cse_results():
            val = r["metrics"]["cse_model_confidence"]
            assert 0.0 <= val <= 1.0, (
                f"Sandbox '{r['sandbox']}': cse_model_confidence={val} out of [0,1]"
            )

    def test_cse_context_nodes_non_negative(self):
        for r in self._cse_results():
            val = r["metrics"]["cse_context_nodes"]
            assert val >= 0, (
                f"Sandbox '{r['sandbox']}': cse_context_nodes={val} is negative"
            )

    def test_node_summary_count_positive(self):
        """At least one node summary must be produced per sandbox."""
        for r in self._cse_results():
            val = r["metrics"]["node_summary_count"]
            assert val > 0, (
                f"Sandbox '{r['sandbox']}': node_summary_count={val} must be > 0"
            )
