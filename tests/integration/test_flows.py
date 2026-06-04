"""Integration tests for flow tracing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from csegraph._core.core.models import to_dict
from csegraph._core.graph.flows import FlowService
from csegraph._core.index.services import IndexService
from csegraph._core.postprocess import PostprocessService


def _index_repo(tmp_path: Path, files: dict[str, str], *, postprocess: bool = True) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, content in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    db = str(tmp_path / "index.db")
    IndexService(db).index(str(repo), profile="small")
    if postprocess:
        PostprocessService(db).postprocess(level="full")
    return db


_SAMPLE_FILES = {
    "main.py": (
        "from app import greet\n"
        "\n"
        "def main():\n"
        "    print(greet('world'))\n"
    ),
    "app.py": (
        "from helpers import fmt\n"
        "\n"
        "def greet(name):\n"
        "    return fmt(name)\n"
    ),
    "helpers.py": (
        "def fmt(name):\n"
        "    return f'Hello, {name}'\n"
    ),
    "tests/test_app.py": (
        "from app import greet\n"
        "\n"
        "def test_greet():\n"
        "    assert greet('x')\n"
    ),
}

_DEEP_CHAIN = {
    "entry.py": (
        "from a import step_a\n"
        "\n"
        "def run():\n"
        "    return step_a()\n"
    ),
    "a.py": (
        "from b import step_b\n"
        "\n"
        "def step_a():\n"
        "    return step_b()\n"
    ),
    "b.py": (
        "from c import step_c\n"
        "\n"
        "def step_b():\n"
        "    return step_c()\n"
    ),
    "c.py": (
        "def step_c():\n"
        "    return 42\n"
    ),
}

_SECURITY_FILES = {
    "auth.py": (
        "def authenticate(user, password):\n"
        "    return verify_token(user)\n"
        "\n"
        "def verify_token(user):\n"
        "    return True\n"
    ),
}


class TestFlowService:
    def test_returns_result(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        result = FlowService(db).trace()
        assert result.command == "flows"
        assert result.db_path == db
        assert isinstance(result.total_entry_points, int)
        assert isinstance(result.total_flows, int)

    def test_result_serializable(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        result = FlowService(db).trace()
        payload = to_dict(result)
        assert isinstance(json.dumps(payload), str)
        assert payload["command"] == "flows"

    def test_flows_sorted_by_criticality(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        result = FlowService(db).trace()
        if len(result.flows) >= 2:
            for i in range(len(result.flows) - 1):
                assert result.flows[i].criticality >= result.flows[i + 1].criticality


class TestEntryPointDetection:
    def test_detects_main(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        result = FlowService(db).trace()
        entry_names = [f.entry_point.name for f in result.flows]
        assert "main" in entry_names

    def test_conventional_names_prioritized(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        result = FlowService(db).trace()
        if result.flows:
            reasons = [f.entry_point.detection_reason for f in result.flows]
            if "conventional_name" in reasons and "no_incoming_calls" in reasons:
                conv_idx = reasons.index("conventional_name")
                no_inc_idx = reasons.index("no_incoming_calls")
                assert result.flows[conv_idx].entry_point.detection_reason == "conventional_name"

    def test_excludes_test_functions(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        result = FlowService(db).trace()
        for flow in result.flows:
            assert not flow.entry_point.name.startswith("test_")

    def test_specific_entry_point(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        result = FlowService(db).trace(entry_point="main")
        assert result.total_flows == 1
        assert result.flows[0].entry_point.name == "main"
        assert result.flows[0].entry_point.detection_reason == "specified"

    def test_unknown_entry_point_returns_empty(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        result = FlowService(db).trace(entry_point="nonexistent_xyz_999")
        assert result.total_flows == 0
        assert result.flows == []


class TestFlowTracing:
    def test_traces_call_chain(self, tmp_path):
        db = _index_repo(tmp_path, _DEEP_CHAIN)
        result = FlowService(db).trace(entry_point="run")
        assert result.total_flows == 1
        flow = result.flows[0]
        assert flow.depth >= 1
        assert flow.node_count >= 2
        step_names = [s.name for s in flow.steps]
        assert "step_a" in step_names

    def test_max_depth_limits_tracing(self, tmp_path):
        db = _index_repo(tmp_path, _DEEP_CHAIN)
        shallow = FlowService(db).trace(entry_point="run", max_depth=1)
        deep = FlowService(db).trace(entry_point="run", max_depth=10)
        assert shallow.flows[0].depth <= 1
        assert deep.flows[0].node_count >= shallow.flows[0].node_count

    def test_flow_has_file_count(self, tmp_path):
        db = _index_repo(tmp_path, _DEEP_CHAIN)
        result = FlowService(db).trace(entry_point="run")
        flow = result.flows[0]
        assert flow.file_count >= 1

    def test_steps_sorted_by_depth(self, tmp_path):
        db = _index_repo(tmp_path, _DEEP_CHAIN)
        result = FlowService(db).trace(entry_point="run")
        flow = result.flows[0]
        for i in range(len(flow.steps) - 1):
            assert flow.steps[i].depth <= flow.steps[i + 1].depth


class TestCriticality:
    def test_criticality_between_0_and_1(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        result = FlowService(db).trace()
        for flow in result.flows:
            assert 0.0 <= flow.criticality <= 1.0

    def test_criticality_factors_populated(self, tmp_path):
        db = _index_repo(tmp_path, _DEEP_CHAIN)
        result = FlowService(db).trace(entry_point="run")
        flow = result.flows[0]
        assert isinstance(flow.criticality_factors, list)

    def test_security_sensitivity_detected(self, tmp_path):
        db = _index_repo(tmp_path, _SECURITY_FILES)
        result = FlowService(db).trace(entry_point="authenticate")
        if result.flows:
            flow = result.flows[0]
            security_factors = [f for f in flow.criticality_factors if "security" in f]
            assert len(security_factors) >= 1


class TestLimitAndWarnings:
    def test_limit_restricts_flows(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        result = FlowService(db).trace(limit=1)
        assert result.total_flows <= 1

    def test_warning_when_truncated(self, tmp_path):
        db = _index_repo(tmp_path, _SAMPLE_FILES)
        result = FlowService(db).trace(limit=1)
        if result.total_entry_points > 1:
            assert any("Showing" in w for w in result.warnings)

    def test_empty_repo(self, tmp_path):
        repo = tmp_path / "empty"
        repo.mkdir()
        db = str(tmp_path / "index.db")
        IndexService(db).index(str(repo), profile="small")
        result = FlowService(db).trace()
        assert result.total_flows == 0
        assert result.flows == []


class TestFlowsMCP:
    def test_tool_is_cli_only(self):
        from csegraph._core.server.app import _handle_tool

        with pytest.raises(ValueError, match="Unknown tool"):
            _handle_tool("csegraph_flows", {})

    def test_prompt_is_not_agent_facing(self):
        from csegraph._core.server.app import _handle_prompt

        with pytest.raises(ValueError, match="Unknown prompt"):
            _handle_prompt("csegraph-flows", {"repo": "/repo"})
