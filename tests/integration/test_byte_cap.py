"""Hard byte cap on MCP responses.

The server enforces a deterministic ceiling on serialized response size and
records what was dropped (source_text, explanations, trimmed nodes/edges) so
agents see the truncation rather than silently getting partial data.
"""

from __future__ import annotations

import json
from pathlib import Path

from csegraph_core.server.app import _TOOLS, _apply_byte_cap, _handle_tool


def _indexed(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text(
        "from b import helper\n"
        "\n"
        "def foo():\n"
        "    return helper()\n",
        encoding="utf-8",
    )
    (repo / "b.py").write_text(
        "def helper():\n    return 1\n",
        encoding="utf-8",
    )
    db = str(tmp_path / "test.db")
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
        result = {
            "nodes": [
                {"id": "x", "source_text": "x" * 2000, "explanation": "explain"}
            ]
        }
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
        _apply_byte_cap(result, 200)
        # Should drop source_text and explanation; may also trim nodes.
        assert "source_text" in result["truncated_fields"]
        assert "explanation" in result["truncated_fields"]
        assert result["byte_cap_applied"] is True

    def test_drop_order_is_stable(self):
        result = {
            "nodes": [
                {"id": "n", "source_text": "x" * 1000, "explanation": "y" * 1000}
            ]
        }
        _apply_byte_cap(result, 200)
        # source_text appears before explanation in the truncated_fields list.
        idx_source = result["truncated_fields"].index("source_text")
        idx_expl = result["truncated_fields"].index("explanation")
        assert idx_source < idx_expl

    def test_response_bytes_matches_actual_size(self):
        result = {
            "nodes": [
                {"id": f"n{i}", "source_text": "x" * 100}
                for i in range(3)
            ]
        }
        _apply_byte_cap(result, 200)
        assert result["response_bytes"] == _encoded(result)


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
        for node in result["nodes"]:
            assert node.get("source_text") in (None, "")


class TestByteCapSchemas:
    def test_context_schema_declares_max_bytes(self):
        ctx = next(t for t in _TOOLS if t.name == "csegraph_context")
        assert "max_bytes" in ctx.inputSchema["properties"]
        assert ctx.inputSchema["properties"]["max_bytes"]["type"] == "integer"

    def test_graph_schema_declares_max_bytes(self):
        gr = next(t for t in _TOOLS if t.name == "csegraph_graph")
        assert "max_bytes" in gr.inputSchema["properties"]

    def test_path_schema_declares_max_bytes(self):
        p = next(t for t in _TOOLS if t.name == "csegraph_path")
        assert "max_bytes" in p.inputSchema["properties"]
