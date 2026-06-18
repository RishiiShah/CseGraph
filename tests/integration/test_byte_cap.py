"""Hard byte cap on MCP responses.

The server enforces a deterministic ceiling on serialized response size and
records what was dropped (source_text, explanations, trimmed nodes/edges) so
agents see the truncation rather than silently getting partial data.
"""

from __future__ import annotations

import json
from pathlib import Path

from csegraph._core.server.app import _TOOLS, _apply_byte_cap, _handle_tool


def _indexed(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text(
        "from b import helper\n\ndef foo():\n    return helper()\n",
        encoding="utf-8",
    )
    (repo / "b.py").write_text(
        "def helper():\n    return 1\n",
        encoding="utf-8",
    )
    db = str(repo / ".scratch" / "csegraph" / "test.db")
    _handle_tool("csegraph_index", {"repo": str(repo), "db": db})
    return repo, db


def _encoded(result: dict) -> int:
    return len(json.dumps(result, default=str).encode("utf-8"))


class TestByteCapHelper:
    def test_no_cap_records_size_only(self):
        result = {"foo": "bar"}
        _apply_byte_cap(result, None)
        assert result["byte_cap_applied"] is False
        assert result["truncated_fields"] == []
        assert result["response_bytes"] == _encoded(result)

    def test_cap_above_payload_no_truncation(self):
        result = {"nodes": [{"id": "x", "source_text": "abc"}]}
        _apply_byte_cap(result, 10000)
        assert result["byte_cap_applied"] is False
        assert result["truncated_fields"] == []
        assert "source_text" in result["nodes"][0]

    def test_drops_source_text_first(self):
        result = {"nodes": [{"id": "x", "source_text": "x" * 2000, "explanation": "explain"}]}
        # Tight enough to require source_text drop but explanation can stay.
        _apply_byte_cap(result, 300)
        assert "source_text" in result["truncated_fields"]
        assert result["byte_cap_applied"] is True
        assert "source_text" not in result["nodes"][0]

    def test_cascades_through_categories(self):
        result = {
            "nodes": [
                {"id": f"n{i}", "source_text": "x" * 500, "explanation": "ex" * 50}
                for i in range(5)
            ]
        }
        _apply_byte_cap(result, 300)
        # Should drop source_text and explanation; may also trim nodes.
        assert "source_text" in result["truncated_fields"]
        assert "explanation" in result["truncated_fields"]
        assert result["byte_cap_applied"] is True

    def test_drop_order_is_stable(self):
        result = {"nodes": [{"id": "n", "source_text": "x" * 1000, "explanation": "y" * 1000}]}
        _apply_byte_cap(result, 300)
        # source_text appears before explanation in the truncated_fields list.
        idx_source = result["truncated_fields"].index("source_text")
        idx_expl = result["truncated_fields"].index("explanation")
        assert idx_source < idx_expl

    def test_response_bytes_matches_actual_size(self):
        result = {"nodes": [{"id": f"n{i}", "source_text": "x" * 100} for i in range(3)]}
        _apply_byte_cap(result, 300)
        assert result["response_bytes"] == _encoded(result)

    def test_small_cap_validated_by_handle_tool(self, tmp_path):
        """Validation of max_bytes < 256 is done in _handle_tool, not _apply_byte_cap."""
        import pytest

        from csegraph._core.server.app import _handle_tool

        repo = tmp_path / "repo"
        repo.mkdir()
        with pytest.raises(ValueError, match="max_bytes must be at least 256"):
            _handle_tool("csegraph_context", {"repo": str(repo), "task": "t", "max_bytes": 100})


class TestByteCapOnMcpResponse:
    def test_no_cap_reports_size(self, tmp_path):
        repo, db = _indexed(tmp_path)
        result = _handle_tool(
            "csegraph_context",
            {"task": "foo", "repo": str(repo), "db": db, "target": "foo"},
        )
        assert result["byte_cap_applied"] is False
        assert result["response_bytes"] == _encoded(result)

    def test_response_bytes_accuracy_with_cap(self, tmp_path):
        repo, db = _indexed(tmp_path)
        result = _handle_tool(
            "csegraph_context",
            {
                "task": "foo",
                "repo": str(repo),
                "db": db,
                "target": "foo",
                "include_source": "always",
                "max_bytes": 1000,
            },
        )
        assert result["response_bytes"] == _encoded(result)
        assert result["byte_cap"] == 1000

    def test_cap_triggers_truncation(self, tmp_path):
        repo, db = _indexed(tmp_path)
        result = _handle_tool(
            "csegraph_context",
            {
                "task": "foo",
                "repo": str(repo),
                "db": db,
                "target": "foo",
                "include_source": "always",
                "detail_level": "full",
                "max_bytes": 800,
            },
        )
        assert result["byte_cap_applied"] is True
        assert "source_text" in result["truncated_fields"]
        # No node retained a source_text after the cap kicked in.
        for node in result.get("nodes", []):
            assert node.get("source_text") in (None, "")
        if "nodes" not in result:
            assert result["omitted_counts"]["nodes"] >= 1

    def test_minimum_mcp_cap_is_hard_for_large_tools(self, tmp_path):
        repo, db = _indexed(tmp_path)
        for tool_name, args in [
            (
                "csegraph_context",
                {
                    "repo": str(repo),
                    "db": db,
                    "task": "foo helper context",
                    "target": "foo",
                    "detail_level": "full",
                    "max_bytes": 256,
                },
            ),
        ]:
            result = _handle_tool(tool_name, args)
            assert result["response_bytes"] == _encoded(result)
            assert result["response_bytes"] <= 256


class TestByteCapSchemas:
    def test_context_schema_declares_max_bytes(self):
        ctx = next(t for t in _TOOLS if t.name == "csegraph_context")
        assert "max_bytes" in ctx.inputSchema["properties"]
        assert ctx.inputSchema["properties"]["max_bytes"]["type"] == "integer"

    def test_all_max_bytes_schemas_declare_minimum(self):
        for tool in _TOOLS:
            prop = tool.inputSchema["properties"].get("max_bytes")
            if prop is not None:
                assert prop["minimum"] == 256

    def test_graph_schema_declares_max_bytes(self):
        gr = next(t for t in _TOOLS if t.name == "csegraph_graph")
        assert "max_bytes" in gr.inputSchema["properties"]

    def test_path_schema_declares_max_bytes(self):
        p = next(t for t in _TOOLS if t.name == "csegraph_path")
        assert "max_bytes" in p.inputSchema["properties"]


class TestGenericListTrim:
    def test_trims_change_detection_style_result(self):
        result = {
            "high_risk": [{"id": "h1"}],
            "medium_risk": [{"id": f"m{i}", "detail": "x" * 200} for i in range(10)],
            "low_risk": [{"id": f"l{i}", "detail": "x" * 200} for i in range(20)],
        }
        _apply_byte_cap(result, 1000)
        assert result["byte_cap_applied"] is True
        assert "low_risk" in result["truncated_fields"]
        assert result["response_bytes"] <= 1000

    def test_change_detection_trims_low_before_high(self):
        result = {
            "high_risk": [{"id": "h1", "detail": "x" * 50}],
            "medium_risk": [{"id": "m1", "detail": "x" * 50}],
            "low_risk": [{"id": f"l{i}", "detail": "x" * 200} for i in range(10)],
        }
        _apply_byte_cap(result, 900)
        assert len(result["high_risk"]) == 1
        assert len(result["medium_risk"]) == 1
        assert len(result["low_risk"]) < 10
        assert "high_risk" not in result["truncated_fields"]
        assert result["response_bytes"] <= 900

    def test_trims_flow_style_result(self):
        result = {
            "flows": [{"name": f"flow{i}", "steps": list(range(50))} for i in range(10)],
        }
        _apply_byte_cap(result, 500)
        assert result["byte_cap_applied"] is True
        assert "flows" in result["truncated_fields"]
        assert len(result["flows"]) < 10
        assert result["response_bytes"] <= 500

    def test_single_oversized_flow_can_be_omitted_to_meet_cap(self):
        result = {
            "flows": [{"name": "one", "steps": ["x" * 5000]}],
            "summary": "x" * 1000,
        }
        _apply_byte_cap(result, 500)
        assert result["flows"] == []
        assert result["omitted_counts"]["flows"] == 1
        assert result["response_bytes"] <= 500

    def test_minimum_accepted_cap_is_still_hard_cap(self):
        result = {
            "flows": [{"name": "one", "steps": ["x" * 5000]}],
            "summary": "x" * 5000,
        }
        _apply_byte_cap(result, 256)
        assert result["response_bytes"] == _encoded(result)
        assert result["response_bytes"] <= 256

    def test_does_not_trim_warnings_or_metadata(self):
        result = {
            "warnings": [f"warn{i}" for i in range(50)],
            "truncated_fields": [],
            "tools_already_called": ["a", "b", "c"],
            "payload": [{"data": "x" * 500} for _ in range(5)],
        }
        _apply_byte_cap(result, 800)
        assert len(result["warnings"]) == 50
        assert "payload" in result["truncated_fields"]
