"""Integration tests for the csegraph_minimal MCP tool and CLI subcommand."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from csegraph._core.core.models import to_dict
from csegraph._core.index.services import IndexService
from csegraph._core.retrieval import minimal as minimal_module
from csegraph._core.retrieval.minimal import MinimalService
from csegraph._core.server.app import _TOOLS, _handle_tool


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "from helpers import fmt\n\ndef greet(name: str) -> str:\n    return fmt(name)\n",
        encoding="utf-8",
    )
    (repo / "helpers.py").write_text(
        'def fmt(name: str) -> str:\n    return f"hi {name}"\n',
        encoding="utf-8",
    )
    return repo


def _indexed(tmp_path: Path) -> tuple[Path, str]:
    repo = _make_repo(tmp_path)
    db = str(repo / ".scratch" / "csegraph" / "test.db")
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
        assert result.index_health is not None
        assert result.index_health.verdict

    def test_explore_intent_includes_suggested_queries(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="explore the architecture")
        assert result.task_intent == "explore"
        assert len(result.suggested_queries) >= 1

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

    def test_key_entities_preserve_kind_from_cached_symbol_rows(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first()
        assert result.key_entities
        assert all(entity.kind for entity in result.key_entities)
        assert {entity.kind for entity in result.key_entities} <= {
            "function",
            "class",
            "method",
            "test",
        }

    def test_general_key_entities_do_not_default_to_tests(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text(
            "def greet(name: str) -> str:\n    return name.title()\n",
            encoding="utf-8",
        )
        tests_dir = repo / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_app.py").write_text(
            "\n".join(
                f"def test_greet_{index}():\n    assert greet('sam') == 'Sam'\n"
                for index in range(8)
            ),
            encoding="utf-8",
        )
        db = str(repo / ".scratch" / "csegraph" / "test.db")
        IndexService(db).index(repo, profile="small")

        result = MinimalService(db).first(task="explore the app architecture")

        assert result.key_entities
        assert all(not entity.path.startswith("tests/") for entity in result.key_entities)


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

    def test_broad_improvement_prompt_routes_to_context_first(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(
            task="What should we improve in the context engine roadmap?"
        )
        assert result.task_intent == "explore"
        assert result.next_tool_suggestions[0].tool == "csegraph_context"

    def test_unmatched_task_falls_back_to_general(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="add a totally unrelated feature")
        assert result.task_intent == "general"
        assert any(s.tool == "csegraph_context" for s in result.next_tool_suggestions)


def _inject_hub(db: str, hub_id: str, caller_count: int) -> None:
    """Make `hub_id` a hub by inserting `caller_count` synthetic callers pointing at it."""
    conn = sqlite3.connect(db)
    try:
        for i in range(caller_count):
            nid = f"symbol::synthetic.py::function::syn_caller_{i}"
            conn.execute(
                """
                INSERT OR IGNORE INTO nodes
                  (id, type, name, path, language, source_hash, updated_at)
                VALUES (?, 'function', ?, 'synthetic.py', 'python', 'synthetic', 0)
                """,
                (nid, nid),
            )
            conn.execute(
                "INSERT OR IGNORE INTO edges "
                "(source, target, relation, confidence, confidence_tier) "
                "VALUES (?, ?, 'calls', 1.0, 'EXTRACTED')",
                (nid, hub_id),
            )
        conn.commit()
    finally:
        conn.close()


class TestHubFilteredKeyEntities:
    def test_reuses_snapshot_hub_cache_for_same_snapshot_version(self, tmp_path):
        _, db = _indexed(tmp_path)
        minimal_module._hub_cache.clear()
        index = minimal_module.ProjectIndex(db)
        index.initialize_schema()
        try:
            from csegraph._core.retrieval.cache import CACHE

            snapshot = CACHE.get_snapshot(index)

            with patch(
                "csegraph._core.retrieval.minimal._snapshot_hub_info",
                wraps=minimal_module._snapshot_hub_info,
            ) as wrapped:
                minimal_module._cached_snapshot_hub_info(snapshot)
                minimal_module._cached_snapshot_hub_info(snapshot)
        finally:
            index.close()

        assert wrapped.call_count == 1

    def test_tiny_repo_with_no_hubs_unaffected(self, tmp_path):
        # Below the floor of 50 → no hubs → key_entities still surface tiny-repo symbols.
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first()
        names = {e.name for e in result.key_entities}
        # The fixture's `greet` and `fmt` should still be visible (no hub filter triggered).
        assert names & {"greet", "fmt"}

    def test_injected_hub_excluded_from_key_entities(self, tmp_path):
        _, db = _indexed(tmp_path)
        hub_id = "symbol::helpers.py::function::fmt"
        _inject_hub(db, hub_id, caller_count=60)
        result = MinimalService(db).first()
        entity_ids = {e.id for e in result.key_entities}
        assert hub_id not in entity_ids

    def test_falls_back_to_fewer_entries_when_all_candidates_are_hubs(self, tmp_path):
        # Force both real symbols above the floor; key_entities should drop them
        # and return only whatever non-hub candidates remain (possibly zero or
        # just the synthetic callers, which are degree-1).
        _, db = _indexed(tmp_path)
        _inject_hub(db, "symbol::helpers.py::function::fmt", caller_count=60)
        _inject_hub(db, "symbol::app.py::function::greet", caller_count=60)
        result = MinimalService(db).first()
        names = {e.name for e in result.key_entities}
        # The two original symbols must NOT be there.
        assert "greet" not in names
        assert "fmt" not in names


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
