"""Tests for the pure helper functions in compare_baselines.py.

Does NOT call run_comparison (which requires live sandbox files and agents).
Only tests the stateless helper functions that are importable directly.
"""

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest

from compare_baselines import (
    _accum_empty,
    _accum_to_means,
    _compile_success_rate,
    _compute_summary,
    _result_to_dict,
    _safe_mean,
    _NUMERIC_FIELDS,
    _STRATEGIES,
)
from models.cse_result import (
    SufficiencyMetrics,
    SufficiencyQuery,
    SufficiencyResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    context_node_count: int = 5,
    raw_code_nodes_count: int = 0,
    dep_completeness: float = 0.9,
    entity_coverage: float = 0.8,
    semantic_overlap: float = 0.6,
    model_confidence: float = 0.7,
    prompt_tokens=None,
    completion_tokens=None,
    total_tokens=None,
    compiled=None,
    compile_error=None,
    is_sufficient: bool = True,
    expansion_rounds: int = 0,
    recompressed_rounds: int = 0,
    mean_logprob=None,
    logprob_triggered_regen: bool = False,
    unit_test_pass_rate=None,
    reason: str = "All thresholds met",
):
    """Build a strategy row dict that matches what _result_to_dict returns."""
    return {
        "context_node_count": context_node_count,
        "raw_code_nodes_count": raw_code_nodes_count,
        "is_sufficient": is_sufficient,
        "expansion_rounds": expansion_rounds,
        "recompressed_rounds": recompressed_rounds,
        "dep_completeness": dep_completeness,
        "entity_coverage": entity_coverage,
        "semantic_overlap": semantic_overlap,
        "model_confidence": model_confidence,
        "reason": reason,
        "generated_code": None,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "compiled": compiled,
        "compile_error": compile_error,
        "mean_logprob": mean_logprob,
        "logprob_triggered_regen": logprob_triggered_regen,
        "unit_test_pass_rate": unit_test_pass_rate,
    }


def _make_all_results(strategies_per_sandbox):
    """Build the all_results list from a list of per-sandbox strategy dicts."""
    results = []
    for i, strategies in enumerate(strategies_per_sandbox):
        results.append(
            {
                "sandbox": f"sandbox_{i}",
                "sandbox_path": f"/tmp/sandbox_{i}",
                "target_node_id": "sym::a",
                "target_file_path": "a.py",
                "query_text": "test query",
                "node_summary_count": 10,
                "strategies": strategies,
            }
        )
    return results


# ---------------------------------------------------------------------------
# Tests: _safe_mean
# ---------------------------------------------------------------------------


class TestSafeMean:
    def test_basic_average(self):
        assert _safe_mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)

    def test_skips_none_values(self):
        assert _safe_mean([None, 2.0, None]) == pytest.approx(2.0)

    def test_all_none_returns_none(self):
        assert _safe_mean([None, None]) is None

    def test_empty_list_returns_none(self):
        assert _safe_mean([]) is None

    def test_single_value(self):
        assert _safe_mean([42.0]) == pytest.approx(42.0)

    def test_integer_values(self):
        assert _safe_mean([1, 3]) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Tests: _compile_success_rate
# ---------------------------------------------------------------------------


class TestCompileSuccessRate:
    def test_two_of_three(self):
        result = _compile_success_rate([True, True, False])
        assert result == pytest.approx(2 / 3)

    def test_all_none_returns_none(self):
        assert _compile_success_rate([None, None]) is None

    def test_single_true(self):
        assert _compile_success_rate([True]) == pytest.approx(1.0)

    def test_empty_returns_none(self):
        assert _compile_success_rate([]) is None

    def test_none_values_skipped(self):
        # None values are excluded; only True/False count
        result = _compile_success_rate([True, None, False])
        assert result == pytest.approx(0.5)

    def test_all_false(self):
        assert _compile_success_rate([False, False]) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Tests: _compute_summary
# ---------------------------------------------------------------------------


class TestComputeSummary:
    def _two_sandbox_results(self):
        """2 sandboxes × 3 strategies with known values for easy assertions."""
        sb0 = {
            "adaptive": _make_row(context_node_count=4, dep_completeness=0.8, compiled=True),
            "full_context": _make_row(context_node_count=10, dep_completeness=1.0, compiled=True),
            "static_rag": _make_row(context_node_count=6, dep_completeness=0.6, compiled=False),
        }
        sb1 = {
            "adaptive": _make_row(context_node_count=8, dep_completeness=1.0, compiled=False),
            "full_context": _make_row(context_node_count=10, dep_completeness=0.9, compiled=True),
            "static_rag": _make_row(context_node_count=5, dep_completeness=0.4, compiled=None),
        }
        return _make_all_results([sb0, sb1])

    def test_has_global_and_per_sandbox_keys(self):
        summary = _compute_summary(self._two_sandbox_results())
        assert "global" in summary
        assert "per_sandbox" in summary

    def test_all_strategy_keys_present(self):
        summary = _compute_summary(self._two_sandbox_results())
        global_summary = summary["global"]
        assert "adaptive" in global_summary
        assert "full_context" in global_summary
        assert "static_rag" in global_summary

    def test_context_node_count_mean_adaptive(self):
        summary = _compute_summary(self._two_sandbox_results())
        # adaptive: (4 + 8) / 2 = 6.0
        assert summary["global"]["adaptive"]["context_node_count"] == pytest.approx(6.0)

    def test_context_node_count_mean_full_context(self):
        summary = _compute_summary(self._two_sandbox_results())
        # full_context: (10 + 10) / 2 = 10.0
        assert summary["global"]["full_context"]["context_node_count"] == pytest.approx(10.0)

    def test_dep_completeness_mean(self):
        summary = _compute_summary(self._two_sandbox_results())
        # static_rag: (0.6 + 0.4) / 2 = 0.5
        assert summary["global"]["static_rag"]["dep_completeness"] == pytest.approx(0.5)

    def test_compile_success_rate_adaptive(self):
        summary = _compute_summary(self._two_sandbox_results())
        # adaptive: True, False → 0.5
        assert summary["global"]["adaptive"]["compile_success_rate"] == pytest.approx(0.5)

    def test_compile_success_rate_full_context(self):
        summary = _compute_summary(self._two_sandbox_results())
        # full_context: True, True → 1.0
        assert summary["global"]["full_context"]["compile_success_rate"] == pytest.approx(1.0)

    def test_compile_success_rate_skips_none(self):
        summary = _compute_summary(self._two_sandbox_results())
        # static_rag: False, None → 0.0 (only False is non-None)
        assert summary["global"]["static_rag"]["compile_success_rate"] == pytest.approx(0.0)

    def test_all_none_field_returns_none(self):
        """Fields that are all None (e.g. prompt_tokens) should yield None mean."""
        summary = _compute_summary(self._two_sandbox_results())
        assert summary["global"]["adaptive"]["prompt_tokens"] is None

    def test_per_sandbox_contains_sandbox_names(self):
        summary = _compute_summary(self._two_sandbox_results())
        per_sandbox = summary["per_sandbox"]
        assert "sandbox_0" in per_sandbox
        assert "sandbox_1" in per_sandbox

    def test_per_sandbox_has_strategy_keys(self):
        summary = _compute_summary(self._two_sandbox_results())
        sb0_data = summary["per_sandbox"]["sandbox_0"]
        assert "adaptive" in sb0_data
        assert "full_context" in sb0_data
        assert "static_rag" in sb0_data

    def test_per_sandbox_values_match_single_entry(self):
        """Per-sandbox means equal the single entry value when only one target."""
        summary = _compute_summary(self._two_sandbox_results())
        # sandbox_0 adaptive: context_node_count=4
        assert summary["per_sandbox"]["sandbox_0"]["adaptive"]["context_node_count"] == pytest.approx(4.0)

    def test_empty_all_results(self):
        """An empty all_results list should produce all-None means."""
        summary = _compute_summary([])
        for strategy in ("adaptive", "full_context", "static_rag"):
            assert strategy in summary["global"]
            for val in summary["global"][strategy].values():
                assert val is None


# ---------------------------------------------------------------------------
# Tests: _result_to_dict
# ---------------------------------------------------------------------------


class TestResultToDict:
    def _make_result(self, context_node_ids=None, raw_code_nodes=None):
        if context_node_ids is None:
            context_node_ids = ["a", "b", "c"]
        if raw_code_nodes is None:
            raw_code_nodes = ["d"]
        return SufficiencyResult(
            is_sufficient=True,
            metrics=SufficiencyMetrics(
                dependency_completeness=0.9,
                entity_coverage=0.8,
                semantic_overlap=0.6,
                model_confidence=0.75,
            ),
            context_node_ids=context_node_ids,
            raw_code_nodes=raw_code_nodes,
            expansion_rounds=1,
            max_rounds=3,
            thresholds={},
            reason="All thresholds met",
            query=SufficiencyQuery(
                query_text="test",
                target_node_id="a",
                target_file_path="a.py",
            ),
        )

    def test_all_expected_keys_present(self):
        result = self._make_result()
        d = _result_to_dict(result)
        expected_keys = {
            "context_node_count",
            "raw_code_nodes_count",
            "is_sufficient",
            "expansion_rounds",
            "recompressed_rounds",
            "dep_completeness",
            "entity_coverage",
            "semantic_overlap",
            "model_confidence",
            "reason",
        }
        assert expected_keys.issubset(d.keys())

    def test_context_node_count_is_sum_of_ids_and_raw(self):
        # context_node_ids = ["a", "b", "c"] (3) + raw_code_nodes = ["d"] (1) → 4
        result = self._make_result(context_node_ids=["a", "b", "c"], raw_code_nodes=["d"])
        d = _result_to_dict(result)
        assert d["context_node_count"] == 4

    def test_context_node_count_empty_raw(self):
        result = self._make_result(context_node_ids=["x", "y"], raw_code_nodes=[])
        d = _result_to_dict(result)
        assert d["context_node_count"] == 2

    def test_metric_values_match(self):
        result = self._make_result()
        d = _result_to_dict(result)
        assert d["dep_completeness"] == pytest.approx(0.9)
        assert d["entity_coverage"] == pytest.approx(0.8)
        assert d["semantic_overlap"] == pytest.approx(0.6)
        assert d["model_confidence"] == pytest.approx(0.75)

    def test_is_sufficient_and_reason(self):
        result = self._make_result()
        d = _result_to_dict(result)
        assert d["is_sufficient"] is True
        assert d["reason"] == "All thresholds met"

    def test_expansion_rounds(self):
        result = self._make_result()
        d = _result_to_dict(result)
        assert d["expansion_rounds"] == 1

    def test_raw_code_nodes_count(self):
        result = self._make_result(context_node_ids=["a", "b"], raw_code_nodes=["c", "d"])
        d = _result_to_dict(result)
        assert d["raw_code_nodes_count"] == 2

    def test_raw_code_nodes_count_zero_when_empty(self):
        result = self._make_result(context_node_ids=["x"], raw_code_nodes=[])
        d = _result_to_dict(result)
        assert d["raw_code_nodes_count"] == 0

    def test_recompressed_rounds_default_zero(self):
        result = self._make_result()
        # SufficiencyResult.recompressed_rounds defaults to 0
        assert result.recompressed_rounds == 0
        d = _result_to_dict(result)
        assert d["recompressed_rounds"] == 0


# ---------------------------------------------------------------------------
# Tests: _accum_empty and _accum_to_means
# ---------------------------------------------------------------------------


class TestAccumHelpers:
    def test_accum_empty_has_all_numeric_fields(self):
        accum = _accum_empty()
        for field in _NUMERIC_FIELDS:
            assert field in accum
            assert accum[field] == []

    def test_accum_empty_has_compiled_key(self):
        accum = _accum_empty()
        assert "compiled" in accum
        assert accum["compiled"] == []

    def test_accum_to_means_basic_average(self):
        accum = _accum_empty()
        accum["context_node_count"] = [4.0, 8.0]
        accum["dep_completeness"] = [0.8, 1.0]
        accum["compiled"] = [True, False]
        means = _accum_to_means(accum)
        assert means["context_node_count"] == pytest.approx(6.0)
        assert means["dep_completeness"] == pytest.approx(0.9)
        assert means["compile_success_rate"] == pytest.approx(0.5)

    def test_accum_to_means_all_none_returns_none(self):
        accum = _accum_empty()
        accum["mean_logprob"] = [None, None]
        means = _accum_to_means(accum)
        assert means["mean_logprob"] is None

    def test_accum_to_means_has_compile_success_rate_not_compiled(self):
        accum = _accum_empty()
        means = _accum_to_means(accum)
        assert "compile_success_rate" in means
        assert "compiled" not in means

    def test_accum_to_means_skips_none_in_numeric(self):
        accum = _accum_empty()
        accum["total_tokens"] = [100, None, 200]
        means = _accum_to_means(accum)
        assert means["total_tokens"] == pytest.approx(150.0)

    def test_expansion_rounds_in_numeric_fields(self):
        assert "expansion_rounds" in _NUMERIC_FIELDS

    def test_recompressed_rounds_in_numeric_fields(self):
        assert "recompressed_rounds" in _NUMERIC_FIELDS

    def test_accum_to_means_expansion_rounds(self):
        accum = _accum_empty()
        accum["expansion_rounds"] = [1, 2, 3]
        means = _accum_to_means(accum)
        assert means["expansion_rounds"] == pytest.approx(2.0)

    def test_accum_to_means_recompressed_rounds(self):
        accum = _accum_empty()
        accum["recompressed_rounds"] = [0, 0, 1]
        means = _accum_to_means(accum)
        assert means["recompressed_rounds"] == pytest.approx(1 / 3)


# ---------------------------------------------------------------------------
# Tests: _compute_summary nested structure consumed by report_plots
# ---------------------------------------------------------------------------


class TestComputeSummaryNestedStructure:
    """Verify the nested {"global": ..., "per_sandbox": ...} shape that
    generate_baseline_plots() must now unpack via raw.get("global", raw)."""

    def _results(self):
        sb = {
            "adaptive": _make_row(context_node_count=6, expansion_rounds=2, unit_test_pass_rate=0.8),
            "full_context": _make_row(context_node_count=10, expansion_rounds=0, unit_test_pass_rate=0.6),
            "static_rag": _make_row(context_node_count=8, expansion_rounds=0, unit_test_pass_rate=0.5),
        }
        return _make_all_results([sb])

    def test_top_level_keys_are_global_and_per_sandbox(self):
        summary = _compute_summary(self._results())
        assert set(summary.keys()) == {"global", "per_sandbox"}

    def test_global_get_fallback_returns_global_dict(self):
        """Simulates what generate_baseline_plots does: raw.get('global', raw)."""
        summary = _compute_summary(self._results())
        global_data = summary.get("global", summary)
        assert "adaptive" in global_data
        assert "full_context" in global_data
        assert "static_rag" in global_data

    def test_global_expansion_rounds_mean(self):
        summary = _compute_summary(self._results())
        global_data = summary["global"]
        assert global_data["adaptive"]["expansion_rounds"] == pytest.approx(2.0)
        assert global_data["full_context"]["expansion_rounds"] == pytest.approx(0.0)

    def test_global_unit_test_pass_rate_mean(self):
        summary = _compute_summary(self._results())
        global_data = summary["global"]
        assert global_data["adaptive"]["unit_test_pass_rate"] == pytest.approx(0.8)
        assert global_data["static_rag"]["unit_test_pass_rate"] == pytest.approx(0.5)

    def test_flat_summary_fallback_still_works(self):
        """If someone passes a flat dict (old format), .get('global', raw) returns
        the flat dict itself — same access pattern works either way."""
        flat = {
            "adaptive": {"context_node_count": 5.0},
            "full_context": {"context_node_count": 10.0},
            "static_rag": {"context_node_count": 7.0},
        }
        unpacked = flat.get("global", flat)
        assert unpacked["adaptive"]["context_node_count"] == 5.0
