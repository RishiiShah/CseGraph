import json

import pytest

from csegraph._core.config.profiles import get_profile, load_profile
from csegraph._core.cse.metrics import SufficiencyMetrics, all_pass, raw_code_nodes


class TestDefaultsMatchConstants:
    def test_medium_profile_cse_defaults(self):
        cfg = get_profile("medium")
        assert cfg.dep_threshold == 0.80
        assert cfg.entity_threshold == 0.80
        assert cfg.semantic_threshold == 0.50
        assert cfg.semantic_threshold_relaxed == 0.0
        assert cfg.confidence_threshold == 0.70
        assert cfg.context_budget == 60

    def test_small_and_large_inherit_cse_defaults(self):
        for name in ("small", "large"):
            cfg = get_profile(name)
            assert cfg.dep_threshold == 0.80
            assert cfg.confidence_threshold == 0.70


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
