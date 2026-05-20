"""Integration tests for the csegraph_minimal MCP tool and CLI subcommand."""

from __future__ import annotations

import json
from pathlib import Path

from csegraph_core.core.models import to_dict
from csegraph_core.index.services import IndexService
from csegraph_core.retrieval.minimal import MinimalService
from csegraph_core.server.app import _TOOLS, _handle_tool


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        'from helpers import fmt\n\ndef greet(name: str) -> str:\n    return fmt(name)\n',
        encoding="utf-8",
    )
    (repo / "helpers.py").write_text(
        'def fmt(name: str) -> str:\n    return f"hi {name}"\n',
        encoding="utf-8",
    )
    return repo


def _indexed(tmp_path: Path) -> tuple[Path, str]:
    repo = _make_repo(tmp_path)
    db = str(tmp_path / "test.db")
    IndexService(db).index(repo, profile="small")
    return repo, db


class TestMinimalServiceShape:
    def test_returns_all_fields(self, tmp_path):
        repo, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="review changes")
        assert result.command == "minimal"
        assert result.summary
        assert result.task == "review changes"
        assert result.task_intent == "review"
        assert result.estimated_tokens > 0
        assert len(result.key_entities) >= 1

    def test_payload_is_compact(self, tmp_path):
        repo, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="debug failing test")
        payload = to_dict(result)
        serialized = json.dumps(payload)
        assert len(serialized) < 4000, f"minimal payload too large: {len(serialized)} chars"

    def test_no_task_returns_general_intent(self, tmp_path):
        repo, db = _indexed(tmp_path)
        result = MinimalService(db).first()
        assert result.task is None
        assert result.task_intent == "general"
        assert result.next_tool_suggestions
        assert result.next_tool_suggestions[0].tool == "csegraph_context"

    def test_key_entities_sorted_by_degree(self, tmp_path):
        repo, db = _indexed(tmp_path)
        result = MinimalService(db).first()
        degrees = [e.degree for e in result.key_entities]
        assert degrees == sorted(degrees, reverse=True)


class TestTaskKeywordRouting:
    def test_review_keyword(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="please review this PR")
        assert result.task_intent == "review"
        tools = [s.tool for s in result.next_tool_suggestions]
        assert "csegraph_refresh" in tools

    def test_debug_keyword(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="this test is failing with a bug")
        assert result.task_intent == "debug"

    def test_refactor_keyword(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="refactor the auth module")
        assert result.task_intent == "refactor"
        tools = [s.tool for s in result.next_tool_suggestions]
        assert "csegraph_graph" in tools

    def test_explore_keyword(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="explore the architecture")
        assert result.task_intent == "explore"

    def test_unmatched_task_falls_back_to_general(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="add a totally unrelated feature")
        assert result.task_intent == "general"
        assert any(s.tool == "csegraph_context" for s in result.next_tool_suggestions)


class TestMinimalMcpTool:
    def test_tool_declared(self):
        names = {tool.name for tool in _TOOLS}
        assert "csegraph_minimal" in names

    def test_tool_schema(self):
        tool = next(t for t in _TOOLS if t.name == "csegraph_minimal")
        props = tool.inputSchema["properties"]
        assert "repo" in props
        assert "task" in props
        assert tool.inputSchema["required"] == ["repo"]

    def test_handle_tool_invokes_service(self, tmp_path):
        repo, db = _indexed(tmp_path)
        result = _handle_tool(
            "csegraph_minimal",
            {"repo": str(repo), "db": db, "task": "review the diff"},
        )
        assert result["command"] == "minimal"
        assert result["task_intent"] == "review"
        assert result["next_tool_suggestions"]
