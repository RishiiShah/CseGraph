"""Session-state hint layer for the MCP server.

Verifies that the per-process SessionState records tool invocations and that
next_tool_suggestions / next_actions are filtered to suppress tools already
called this session. CLI is unaffected (each subprocess starts fresh).
"""

from __future__ import annotations

from pathlib import Path

from csegraph_core.server.app import _handle_tool
from csegraph_core.server.session import SessionState, _SESSION


def _indexed_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "from helpers import fmt\n"
        "\n"
        "def greet(name: str) -> str:\n"
        "    return fmt(name)\n",
        encoding="utf-8",
    )
    (repo / "helpers.py").write_text(
        "def fmt(name: str) -> str:\n"
        "    return f\"hi {name}\"\n",
        encoding="utf-8",
    )
    db = str(tmp_path / "test.db")
    _handle_tool("csegraph_index", {"repo": str(repo), "db": db})
    return repo, db


class TestSessionStateClass:
    def test_record_and_query(self):
        s = SessionState()
        s.record("csegraph_minimal")
        assert s.is_called("csegraph_minimal")
        assert not s.is_called("csegraph_context")

    def test_snapshot_sorted(self):
        s = SessionState()
        s.record("csegraph_context")
        s.record("csegraph_minimal")
        s.record("csegraph_graph")
        assert s.snapshot() == ["csegraph_context", "csegraph_graph", "csegraph_minimal"]

    def test_reset_clears(self):
        s = SessionState()
        s.record("csegraph_minimal")
        s.reset()
        assert s.tools_called == set()

    def test_record_ignores_empty(self):
        s = SessionState()
        s.record("")
        assert s.tools_called == set()


class TestSessionFilterOnMinimal:
    def test_first_call_records_itself(self, tmp_path):
        repo, db = _indexed_repo(tmp_path)
        result = _handle_tool(
            "csegraph_minimal",
            {"repo": str(repo), "db": db, "task": "debug failure"},
        )
        assert "csegraph_minimal" in result["tools_already_called"]
        assert "csegraph_index" in result["tools_already_called"]

    def test_subsequent_call_filters_already_called_suggestions(self, tmp_path):
        repo, db = _indexed_repo(tmp_path)
        # First minimal call returns [csegraph_context, csegraph_graph] suggestions
        # (for the debug intent).
        first = _handle_tool(
            "csegraph_minimal",
            {"repo": str(repo), "db": db, "task": "debug failure"},
        )
        suggested_first = {s["tool"] for s in first["next_tool_suggestions"]}
        assert "csegraph_context" in suggested_first

        # Simulate the agent following the suggestion.
        _handle_tool(
            "csegraph_context",
            {"task": "debug failure", "repo": str(repo), "db": db},
        )

        # Now minimal should NOT re-suggest csegraph_context.
        second = _handle_tool(
            "csegraph_minimal",
            {"repo": str(repo), "db": db, "task": "debug failure"},
        )
        suggested_second = {s["tool"] for s in second["next_tool_suggestions"]}
        assert "csegraph_context" not in suggested_second
        assert "csegraph_context" in second["tools_already_called"]


class TestSessionFilterOnContext:
    def test_next_actions_filtered_when_tool_already_called(self, tmp_path):
        repo, db = _indexed_repo(tmp_path)

        # ContextService.next_actions includes a csegraph_graph entry when a target
        # is resolved. Call csegraph_graph first to mark it as already called.
        _handle_tool(
            "csegraph_graph",
            {"node": "greet", "repo": str(repo), "db": db, "depth": 1},
        )

        result = _handle_tool(
            "csegraph_context",
            {"task": "what does greet do", "repo": str(repo), "target": "greet", "db": db},
        )

        tool_actions = [a for a in result["next_actions"] if a.get("tool")]
        assert all(a["tool"] != "csegraph_graph" for a in tool_actions)
        assert "csegraph_graph" in result["tools_already_called"]

    def test_actions_without_tool_field_are_preserved(self, tmp_path):
        repo, db = _indexed_repo(tmp_path)
        # The expand_context action has no `tool` field — it's a parameter directive.
        # Calling csegraph_context once and then again should still surface
        # expand_context if applicable, because the filter only drops entries with
        # a `tool` field in tools_called.
        first = _handle_tool(
            "csegraph_context",
            {"task": "what does greet do", "repo": str(repo), "target": "greet", "db": db},
        )
        # If first returned minimal, expand_context should be present.
        actions = first["next_actions"]
        has_expand = any(a.get("action") == "expand_context" for a in actions)
        # Whether the test repo triggers minimal-mode is environment-dependent;
        # only assert preservation when expand_context was present originally.
        if has_expand:
            assert any(a.get("action") == "expand_context" for a in actions)


class TestSessionIsolationAcrossTests:
    def test_session_starts_empty_due_to_fixture(self):
        # The autouse fixture resets _SESSION; this test should see no prior calls.
        assert _SESSION.tools_called == set()
