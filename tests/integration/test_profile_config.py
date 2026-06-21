import json

import pytest

from csegraph._core.config.profiles import (
    _profile_name_for_source_file_count,
    get_profile,
    load_profile,
    resolve_profile_name,
)
from csegraph._core.cse.metrics import SufficiencyMetrics, all_pass, raw_code_nodes


class TestDefaultsMatchConstants:
    def test_medium_profile_cse_defaults(self):
        cfg = get_profile("medium")
        assert cfg.dep_threshold == 0.80
        assert cfg.entity_threshold == 0.80
        assert cfg.semantic_threshold == 0.50
        assert cfg.semantic_threshold_relaxed == 0.03
        assert cfg.confidence_threshold == 0.70
        assert cfg.context_budget == 60

    def test_profile_relaxed_semantic_defaults_are_active(self):
        assert get_profile("small").semantic_threshold_relaxed == 0.05
        assert get_profile("medium").semantic_threshold_relaxed == 0.03
        assert get_profile("large").semantic_threshold_relaxed == 0.02


class TestLoadProfile:
    def test_override_from_json(self, tmp_path):
        config_file = tmp_path / "csegraph.json"
        config_file.write_text(
            json.dumps(
                {
                    "dep_threshold": 0.70,
                    "confidence_threshold": 0.60,
                    "context_budget": 100,
                }
            ),
            encoding="utf-8",
        )
        cfg = load_profile(config_path=str(config_file))
        assert cfg.dep_threshold == 0.70
        assert cfg.confidence_threshold == 0.60
        assert cfg.context_budget == 100
        assert cfg.entity_threshold == 0.80

    def test_override_from_toml(self, tmp_path):
        config_file = tmp_path / "csegraph.toml"
        config_file.write_text(
            "\n".join(
                [
                    "dep_threshold = 0.70",
                    "confidence_threshold = 0.60",
                    "context_budget = 100",
                ]
            ),
            encoding="utf-8",
        )

        cfg = load_profile(config_path=str(config_file))

        assert cfg.dep_threshold == 0.70
        assert cfg.confidence_threshold == 0.60
        assert cfg.context_budget == 100
        assert cfg.entity_threshold == 0.80

    def test_unknown_keys_raise_valueerror(self, tmp_path):
        config_file = tmp_path / "csegraph.json"
        config_file.write_text(json.dumps({"bogus_key": 42}), encoding="utf-8")
        with pytest.raises(ValueError, match="Unknown config keys"):
            load_profile(config_path=str(config_file))

    def test_explicit_name_wins_over_config_profile(self, tmp_path):
        config_file = tmp_path / "csegraph.json"
        config_file.write_text(json.dumps({"profile": "large"}), encoding="utf-8")
        cfg = load_profile("small", config_path=str(config_file))
        assert cfg.name == "small"
        assert cfg.top_k == 8

    def test_config_profile_used_when_name_is_none(self, tmp_path):
        config_file = tmp_path / "csegraph.json"
        config_file.write_text(json.dumps({"profile": "large"}), encoding="utf-8")
        cfg = load_profile(config_path=str(config_file))
        assert cfg.name == "large"
        assert cfg.top_k == 40

    def test_default_used_when_no_name_no_config_profile(self, tmp_path):
        config_file = tmp_path / "csegraph.json"
        config_file.write_text(json.dumps({"dep_threshold": 0.70}), encoding="utf-8")
        cfg = load_profile(config_path=str(config_file))
        assert cfg.name == "medium"
        assert cfg.dep_threshold == 0.70

    def test_no_config_returns_default_profile(self):
        cfg = load_profile()
        assert cfg.name == "medium"
        assert cfg.dep_threshold == 0.80

    def test_missing_explicit_config_raises_fnf(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_profile(config_path=str(tmp_path / "nonexistent.json"))

    def test_repo_root_discovery(self, tmp_path):
        config_file = tmp_path / "csegraph.json"
        config_file.write_text(json.dumps({"dep_threshold": 0.65}), encoding="utf-8")
        cfg = load_profile(repo_root=str(tmp_path))
        assert cfg.dep_threshold == 0.65

    def test_relaxed_semantic_threshold_can_be_disabled(self, tmp_path):
        config_file = tmp_path / "csegraph.json"
        config_file.write_text(json.dumps({"semantic_threshold_relaxed": 0.0}), encoding="utf-8")
        cfg = load_profile(config_path=str(config_file))
        assert cfg.semantic_threshold_relaxed == 0.0

    def test_auto_profile_uses_tiny_repo_size(self, tmp_path):
        (tmp_path / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")

        cfg = load_profile("auto", repo_root=tmp_path)

        assert cfg.name == "small"
        assert cfg.context_budget == get_profile("small").context_budget

    def test_auto_profile_source_count_boundaries(self):
        assert _profile_name_for_source_file_count(499) == "small"
        assert _profile_name_for_source_file_count(500) == "medium"
        assert _profile_name_for_source_file_count(4999) == "medium"
        assert _profile_name_for_source_file_count(5000) == "large"

    def test_auto_profile_without_repo_defaults_to_medium(self):
        cfg = load_profile("auto")

        assert cfg.name == "medium"

    def test_config_profile_auto_accepts_overrides(self, tmp_path):
        config_file = tmp_path / "csegraph.json"
        config_file.write_text(
            json.dumps({"profile": "auto", "context_budget": 77}),
            encoding="utf-8",
        )

        cfg = load_profile(config_path=str(config_file), source_file_count=5000)

        assert cfg.name == "large"
        assert cfg.context_budget == 77

    def test_resolve_profile_name_reports_auto_as_valid_selector(self):
        with pytest.raises(ValueError, match="auto, small, medium, large"):
            resolve_profile_name("enormous")


class TestAllPassOverrides:
    def test_default_thresholds_reject_low_scores(self):
        metrics = SufficiencyMetrics(
            dependency_completeness=0.75,
            entity_coverage=0.75,
            semantic_overlap=0.50,
            model_confidence=0.70,
        )
        assert not all_pass(metrics)

    def test_lowered_thresholds_accept_low_scores(self):
        metrics = SufficiencyMetrics(
            dependency_completeness=0.75,
            entity_coverage=0.75,
            semantic_overlap=0.50,
            model_confidence=0.70,
        )
        assert all_pass(metrics, dep_threshold=0.70, entity_threshold=0.70)

    def test_semantic_relaxed_override(self):
        metrics = SufficiencyMetrics(
            dependency_completeness=0.90,
            entity_coverage=0.90,
            semantic_overlap=0.10,
            model_confidence=0.75,
        )
        assert all_pass(metrics)
        assert not all_pass(metrics, semantic_threshold_relaxed=0.50)

    def test_default_relaxed_semantic_threshold_rejects_zero_overlap(self):
        metrics = SufficiencyMetrics(
            dependency_completeness=0.90,
            entity_coverage=0.90,
            semantic_overlap=0.0,
            model_confidence=0.75,
        )
        assert not all_pass(metrics)

    def test_raw_code_nodes_uses_confidence_override(self):
        metrics = SufficiencyMetrics(
            dependency_completeness=0.90,
            entity_coverage=0.90,
            semantic_overlap=0.90,
            model_confidence=0.65,
        )
        target = "symbol::main.py::function::func_a"
        outgoing = {target: [{"relation": "calls", "target_id": "func_b"}]}
        context = ["func_b"]
        nodes = raw_code_nodes(target, context, outgoing, metrics, budget=5)
        assert "func_b" in nodes
        nodes2 = raw_code_nodes(
            target,
            context,
            outgoing,
            metrics,
            budget=5,
            confidence_threshold=0.60,
        )
        assert len(nodes2) == 0
