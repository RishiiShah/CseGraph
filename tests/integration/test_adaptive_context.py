from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from csegraph import (
    AsyncContextService,
    ContextRequest,
    ContextService,
    ContextStatus,
    IndexService,
    to_dict,
)
from csegraph._core.index.migrations import migrate_schema
from csegraph._core.index.schema import SCHEMA_VERSION
from csegraph._core.retrieval.freshness import FreshnessCoordinator
from csegraph._core.retrieval.token_budget import count_payload_tokens
from csegraph._core.server.app import _handle_tool


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "helpers.py").write_text(
        "def fmt(name: str) -> str:\n"
        "    return f'hi {name}'\n",
        encoding="utf-8",
    )
    (repo / "app.py").write_text(
        "from helpers import fmt\n\n"
        "def greet(name: str) -> str:\n"
        "    return fmt(name)\n",
        encoding="utf-8",
    )
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text(
        "from app import greet\n\n"
        "def test_greet():\n"
        "    assert greet('sam') == 'hi sam'\n",
        encoding="utf-8",
    )
    db = str(repo / ".scratch" / "csegraph" / "test.db")
    IndexService(db).index(repo, profile="small")
    return repo, db


def test_adaptive_context_is_exact_budget_and_does_not_load_snapshot(tmp_path: Path):
    repo, db = _repo(tmp_path)
    request = ContextRequest(
        repo=str(repo),
        task="Explain greet",
        target="greet",
        token_budget=800,
    )

    with patch(
        "csegraph._core.retrieval.cache.SnapshotManager.get_snapshot",
        side_effect=AssertionError("adaptive retrieval loaded the full graph snapshot"),
    ):
        result = ContextService(db).retrieve(request)

    payload = to_dict(result)
    assert result.status == ContextStatus.READY
    assert result.target is not None
    assert result.target.name == "greet"
    assert result.slices[0].role == "target"
    assert "def greet" in result.slices[0].code
    assert result.usage["tokens"] <= 800
    assert result.usage["tokens"] == count_payload_tokens(payload, "o200k_base")


def test_adaptive_context_returns_ambiguous_candidates(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def handle():\n    return 'a'\n", encoding="utf-8")
    (repo / "b.py").write_text("def handle():\n    return 'b'\n", encoding="utf-8")
    db = str(repo / ".scratch" / "csegraph" / "test.db")
    IndexService(db).index(repo, profile="small")

    result = ContextService(db).retrieve(
        ContextRequest(repo=str(repo), task="Update handle")
    )

    assert result.status == ContextStatus.AMBIGUOUS
    assert len(result.candidates) == 2
    assert result.slices == []


def test_adaptive_continuation_omits_previously_emitted_slices(tmp_path: Path):
    repo, db = _repo(tmp_path)
    service = ContextService(db)
    first = service.retrieve(
        ContextRequest(
            repo=str(repo),
            task="Fix greet and its dependency",
            target="greet",
            token_budget=800,
        )
    )
    second = service.retrieve(
        ContextRequest(
            repo=str(repo),
            task="Fix greet and its dependency",
            target="greet",
            token_budget=800,
            cursor=first.cursor,
        )
    )

    first_keys = {(item.path, tuple(item.lines or [])) for item in first.slices}
    second_keys = {(item.path, tuple(item.lines or [])) for item in second.slices}
    assert first.cursor
    assert first_keys.isdisjoint(second_keys)


def test_adaptive_query_auto_refreshes_modified_source(tmp_path: Path):
    repo, db = _repo(tmp_path)
    app = repo / "app.py"
    app.write_text(
        "from helpers import fmt\n\n"
        "def greet(name: str) -> str:\n"
        "    return fmt(name).upper()\n",
        encoding="utf-8",
    )

    result = ContextService(db).retrieve(
        ContextRequest(repo=str(repo), task="Explain greet", target="greet")
    )

    assert result.status == ContextStatus.READY
    assert result.freshness["state"] == "refreshed"
    assert ".upper()" in result.slices[0].code


def test_mcp_defaults_to_compact_and_supports_explicit_legacy(tmp_path: Path):
    repo, db = _repo(tmp_path)
    compact = _handle_tool(
        "csegraph_context",
        {"repo": str(repo), "db": db, "task": "Explain greet", "target": "greet"},
    )
    legacy = _handle_tool(
        "csegraph_context",
        {
            "repo": str(repo),
            "db": db,
            "task": "Explain greet",
            "target": "greet",
            "response_mode": "legacy-v3",
        },
    )

    assert compact["schema_version"] == "csegraph-context-v4"
    assert compact["status"] == "ready"
    assert compact["usage"]["tokens"] == count_payload_tokens(compact, "o200k_base")
    assert legacy["schema_version"] == "csegraph-context-v3"
    assert "symbols" in legacy


def test_compact_payload_serializes_without_empty_optional_sections(tmp_path: Path):
    repo, db = _repo(tmp_path)
    result = ContextService(db).retrieve(
        ContextRequest(repo=str(repo), task="Explain greet", target="greet")
    )
    payload = to_dict(result)

    assert "candidates" not in payload
    assert "missing" not in payload
    assert "diagnostic" not in payload
    json.dumps(payload)


def test_async_adaptive_context(tmp_path: Path):
    repo, db = _repo(tmp_path)
    result = asyncio.run(
        AsyncContextService(db).retrieve(
            ContextRequest(repo=str(repo), task="Explain greet", target="greet")
        )
    )
    assert result.status == ContextStatus.READY


def test_oversized_target_is_insufficient_without_partial_source(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    body = "\n".join(f"    value_{index} = {index}" for index in range(400))
    (repo / "large.py").write_text(
        f"def enormous():\n{body}\n    return value_399\n",
        encoding="utf-8",
    )
    db = str(repo / ".scratch" / "csegraph" / "test.db")
    IndexService(db).index(repo, profile="small")

    result = ContextService(db).retrieve(
        ContextRequest(
            repo=str(repo),
            task="Update enormous",
            target="enormous",
            token_budget=256,
        )
    )

    assert result.status == ContextStatus.INSUFFICIENT
    assert result.slices == []
    assert result.missing[0]["kind"] == "target_source"
    assert result.usage["tokens"] <= 256


def test_exact_budget_accepts_code_that_looks_like_special_tokens(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "special.py").write_text(
        "def marker():\n    return '<|endoftext|>'\n",
        encoding="utf-8",
    )
    db = str(repo / ".scratch" / "csegraph" / "test.db")
    IndexService(db).index(repo, profile="small")

    result = ContextService(db).retrieve(
        ContextRequest(repo=str(repo), task="Explain marker", target="marker")
    )
    assert result.status == ContextStatus.READY
    assert "<|endoftext|>" in result.slices[0].code


def test_bounded_bootstrap_refuses_repo_above_configured_limit(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "one.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "two.py").write_text("x = 2\n", encoding="utf-8")
    db = repo / ".csegraph" / "index.db"

    result = FreshnessCoordinator(db, auto_index_file_limit=1).ensure_current(repo)

    assert result.status == "index_required"
    assert not db.exists()


def test_v8_migration_adds_adaptive_cache_and_continuation_columns(tmp_path: Path):
    db = tmp_path / "v8.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO metadata(key, value)
            VALUES('schema_version', 'csegraph-sqlite-v8');
            CREATE TABLE retrieval_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                target TEXT,
                profile TEXT NOT NULL,
                dependency_completeness REAL NOT NULL,
                entity_coverage REAL NOT NULL,
                semantic_overlap REAL NOT NULL,
                model_confidence REAL NOT NULL,
                sufficient INTEGER NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE retrieval_context (
                run_id INTEGER NOT NULL,
                node_id TEXT NOT NULL,
                rank INTEGER NOT NULL,
                score REAL NOT NULL,
                raw_code INTEGER NOT NULL,
                evidence TEXT NOT NULL,
                PRIMARY KEY(run_id, node_id)
            );
            """
        )
        migrate_schema(conn, "csegraph-sqlite-v8")
        conn.commit()

        version = conn.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0]
        run_columns = {row[1] for row in conn.execute("PRAGMA table_info(retrieval_runs)")}
        context_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(retrieval_context)")
        }
        cache_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='retrieval_plan_cache'"
        ).fetchone()

    assert version == SCHEMA_VERSION
    assert {"engine", "index_revision", "response_tokens", "cursor"} <= run_columns
    assert {"source_hash", "start_line", "end_line"} <= context_columns
    assert cache_table is not None


def test_future_schema_is_not_silently_reindexed(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db = repo / ".csegraph" / "index.db"
    db.parent.mkdir()
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value)
            VALUES('schema_version', 'csegraph-sqlite-v999');
            """
        )

    with pytest.raises(Exception, match="schema"):
        ContextService(db).retrieve(
            ContextRequest(repo=str(repo), task="Explain the project")
        )
