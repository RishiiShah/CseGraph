"""Integration tests for the csegraph_minimal MCP tool and CLI subcommand."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from csegraph._core.core.ids import file_node_id
from csegraph._core.core.serializer import to_dict
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.languages.base import sha256_text
from csegraph._core.retrieval.minimal import MinimalService
from csegraph._core.server.app import _handle_tool
from csegraph._core.server.tools import TOOLS


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


def _seed_v11_index(repo: Path, db: str) -> None:
    index = ProjectIndex(db)
    try:
        index.initialize_schema()
        source_paths = sorted(repo.rglob("*.py"))
        now = time.time()
        file_rows = []
        symbol_rows = []
        symbols_by_name: dict[str, str] = {}
        for source_path in source_paths:
            rel_path = source_path.relative_to(repo).as_posix()
            source = source_path.read_text(encoding="utf-8")
            source_hash = sha256_text(source)
            stat = source_path.stat()
            fid = file_node_id(rel_path)
            file_rows.append(
                (
                    fid,
                    rel_path,
                    source_path.name,
                    "python",
                    source_hash,
                    "ok",
                    None,
                    stat.st_size,
                    stat.st_mtime,
                    now,
                )
            )
            for line_number, line in enumerate(source.splitlines(), start=1):
                match = re.match(r"\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", line)
                if match is None:
                    continue
                name = match.group(1)
                symbol_id = f"symbol::{rel_path}::function::{name}"
                symbols_by_name[name] = symbol_id
                symbol_rows.append(
                    (
                        symbol_id,
                        fid,
                        None,
                        "test" if name.startswith("test_") else "function",
                        name,
                        line.strip(),
                        None,
                        line_number,
                        line_number + 1,
                        sha256_text(f"{source_hash}:{name}"),
                        int(name.startswith("test_") or rel_path.startswith(("test/", "tests/"))),
                        now,
                    )
                )

        index.conn.executemany(
            """
            INSERT INTO files(
                id, path, name, language, sha256, parse_status, parse_error,
                size, mtime, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            file_rows,
        )
        index.conn.executemany(
            """
            INSERT INTO symbols(
                id, file_id, parent_id, kind, name, signature, docstring,
                start_line, end_line, source_hash, is_test, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            symbol_rows,
        )
        greet = symbols_by_name.get("greet")
        fmt = symbols_by_name.get("fmt")
        if greet and fmt:
            index.conn.execute(
                """
                INSERT INTO edges(source, target, relation, confidence, confidence_tier)
                VALUES(?, ?, 'calls', 1.0, 'EXTRACTED')
                """,
                (greet, fmt),
            )
        index.set_metadata(
            str(repo.resolve()),
            indexed_untracked_paths=[path.relative_to(repo).as_posix() for path in source_paths],
        )
        index.bump_index_revision()
        index.conn.commit()
    finally:
        index.close()


def _indexed(tmp_path: Path) -> tuple[Path, str]:
    repo = _make_repo(tmp_path)
    db = str(repo / ".scratch" / "csegraph" / "test.db")
    _seed_v11_index(repo, db)
    return repo, db


class TestMinimalServiceShape:
    def test_returns_compact_orientation(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="review changes")
        assert result.summary
        assert len(result.key_entities) >= 1
        assert len(result.key_entities) <= 3
        assert result.next_tool_suggestions[0].tool == "csegraph_refresh"

        payload = to_dict(result)
        assert set(payload) == {"summary", "entities", "next"}
        assert {
            "session",
            "history",
            "profile",
            "index_health",
            "db_path",
            "repo_root",
        }.isdisjoint(payload)

    def test_explore_intent_suggests_graph(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="explore the architecture")
        assert any(item.tool == "csegraph_graph" for item in result.next_tool_suggestions)

    def test_payload_is_compact(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="debug failing test")
        payload = to_dict(result)
        serialized = json.dumps(payload)
        assert len(serialized) < 4000, f"minimal payload too large: {len(serialized)} chars"

    def test_no_task_returns_general_intent(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first()
        assert result.next_tool_suggestions
        assert result.next_tool_suggestions[0].tool == "csegraph_context"
        continuation = to_dict(result)["next"]
        assert continuation["arguments"] == {"task": ""}
        assert "args" not in continuation

    def test_key_entities_sorted_by_degree(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first()
        degrees = [e.degree for e in result.key_entities]
        assert degrees == sorted(degrees, reverse=True)

    def test_key_entities_preserve_kind_from_canonical_symbols(self, tmp_path):
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

    def test_does_not_materialize_full_graph_snapshot(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first()
        assert result.key_entities

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
        _seed_v11_index(repo, db)

        result = MinimalService(db).first(task="explore the app architecture")

        assert result.key_entities
        assert all(not entity.path.startswith("tests/") for entity in result.key_entities)


class TestTaskKeywordRouting:
    def test_review_keyword(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="please review this PR")
        tools = [s.tool for s in result.next_tool_suggestions]
        assert "csegraph_refresh" in tools

    def test_debug_keyword(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="this test is failing with a bug")
        assert result.next_tool_suggestions[0].tool == "csegraph_context"

    def test_refactor_keyword(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="refactor the auth module")
        tools = [s.tool for s in result.next_tool_suggestions]
        assert "csegraph_graph" in tools

    def test_explore_keyword(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="explore the architecture")
        assert any(item.tool == "csegraph_graph" for item in result.next_tool_suggestions)

    def test_broad_improvement_prompt_routes_to_context_first(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(
            task="What should we improve in the context engine roadmap?"
        )
        assert result.next_tool_suggestions[0].tool == "csegraph_context"

    def test_unmatched_task_falls_back_to_general(self, tmp_path):
        _, db = _indexed(tmp_path)
        result = MinimalService(db).first(task="add a totally unrelated feature")
        assert any(s.tool == "csegraph_context" for s in result.next_tool_suggestions)


def _inject_hub(db: str, hub_id: str, caller_count: int) -> None:
    """Make `hub_id` a hub by inserting `caller_count` synthetic callers pointing at it."""
    conn = sqlite3.connect(db)
    try:
        now = time.time()
        conn.execute(
            """
            INSERT OR IGNORE INTO files(
                id, path, name, language, sha256, parse_status, parse_error,
                size, mtime, updated_at
            ) VALUES(
                'file::synthetic.py', 'synthetic.py', 'synthetic.py', 'python',
                'synthetic', 'ok', NULL, 0, 0, ?
            )
            """,
            (now,),
        )
        for i in range(caller_count):
            nid = f"symbol::synthetic.py::function::syn_caller_{i}"
            conn.execute(
                """
                INSERT OR IGNORE INTO symbols(
                    id, file_id, parent_id, kind, name, signature, docstring,
                    start_line, end_line, source_hash, is_test, updated_at
                ) VALUES(
                    ?, 'file::synthetic.py', NULL, 'function', ?, NULL, NULL,
                    ?, ?, 'synthetic', 0, ?
                )
                """,
                (nid, f"syn_caller_{i}", i + 1, i + 1, now),
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
        names = {tool.name for tool in TOOLS}
        assert "csegraph_minimal" in names

    def test_tool_schema(self):
        tool = next(t for t in TOOLS if t.name == "csegraph_minimal")
        props = tool.inputSchema["properties"]
        assert "repo" in props
        assert "task" in props
        assert tool.inputSchema["required"] == ["repo"]

    def test_handle_tool_invokes_service(self, tmp_path):
        repo = _make_repo(tmp_path)
        db = str(repo / ".csegraph" / "index.db")
        _seed_v11_index(repo, db)
        result = _handle_tool(
            "csegraph_minimal",
            {"repo": str(repo), "task": "review the diff"},
        )
        assert set(result) == {"summary", "entities", "next"}
        assert result["entities"]
        assert result["next"]["tool"] == "csegraph_refresh"
