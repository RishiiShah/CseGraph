from __future__ import annotations

from pathlib import Path

from csegraph._core.server.app import _handle_tool
from csegraph._core.server.session import _SESSION


def _indexed_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "from helpers import format_name\n\n"
        "def greet(name: str) -> str:\n"
        "    return format_name(name)\n",
        encoding="utf-8",
    )
    (repo / "helpers.py").write_text(
        "def format_name(name: str) -> str:\n    return name.strip().title()\n",
        encoding="utf-8",
    )
    db = str(repo / ".scratch" / "csegraph" / "test.db")
    _handle_tool("csegraph_index", {"repo": str(repo), "db": db})
    return repo, db


class TestSessionStateClass:
    def test_record_and_query(self):
        _SESSION.record("csegraph_minimal")

        assert _SESSION.is_called("csegraph_minimal") is True
        assert _SESSION.is_called("csegraph_context") is False

    def test_snapshot_sorted(self):
        _SESSION.record("csegraph_path")
        _SESSION.record("csegraph_index")
        _SESSION.record("csegraph_context")

        assert _SESSION.snapshot() == ["csegraph_context", "csegraph_index", "csegraph_path"]

    def test_reset_clears(self):
        _SESSION.record("csegraph_minimal")
        _SESSION.inferred_intent = "debug"

        _SESSION.reset()

        assert _SESSION.snapshot() == []
        assert _SESSION.inferred_intent is None

    def test_record_ignores_empty(self):
        _SESSION.record("")

        assert _SESSION.snapshot() == []


class TestSessionFilterOnMinimal:
    def test_first_call_records_itself(self, tmp_path: Path):
        repo, db = _indexed_repo(tmp_path)

        result = _handle_tool(
            "csegraph_minimal",
            {"repo": str(repo), "db": db, "task": "debug failing test"},
        )

        assert result["tools_already_called"] == ["csegraph_index", "csegraph_minimal"]

    def test_subsequent_call_filters_already_called_suggestions(self, tmp_path: Path):
        repo, db = _indexed_repo(tmp_path)

        _handle_tool(
            "csegraph_minimal", {"repo": str(repo), "db": db, "task": "debug failing test"}
        )
        _handle_tool(
            "csegraph_context", {"repo": str(repo), "db": db, "task": "debug failing test"}
        )
        result = _handle_tool(
            "csegraph_minimal", {"repo": str(repo), "db": db, "task": "debug failing test"}
        )

        suggestions = result["next_tool_suggestions"]
        assert all(suggestion.get("tool") != "csegraph_context" for suggestion in suggestions)


class TestSessionFilterOnContext:
    def test_next_actions_filtered_when_tool_already_called(self, tmp_path: Path):
        repo, db = _indexed_repo(tmp_path)

        _handle_tool(
            "csegraph_graph",
            {"repo": str(repo), "db": db, "node": "greet", "detail_level": "minimal"},
        )
        result = _handle_tool(
            "csegraph_context",
            {
                "repo": str(repo),
                "db": db,
                "task": "debug greet",
                "target": "greet",
                "detail_level": "minimal",
            },
        )

        assert all(
            action.get("tool") != "csegraph_graph"
            for action in result["next_actions"]
            if isinstance(action, dict)
        )

    def test_expand_context_action_is_preserved_for_detail_escalation(self, tmp_path: Path):
        repo, db = _indexed_repo(tmp_path)

        _handle_tool(
            "csegraph_context",
            {
                "repo": str(repo),
                "db": db,
                "task": "debug greet",
                "target": "greet",
                "detail_level": "minimal",
            },
        )
        result = _handle_tool(
            "csegraph_context",
            {
                "repo": str(repo),
                "db": db,
                "task": "debug greet",
                "target": "greet",
                "detail_level": "minimal",
            },
        )

        assert any(action.get("action") == "expand_context" for action in result["next_actions"])


class TestSessionIsolationAcrossTests:
    def test_session_starts_empty_due_to_fixture(self):
        assert _SESSION.tools_called == set()
