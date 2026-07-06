from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from csegraph import IndexRequiredError, IndexService
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.index.schema import SCHEMA_USER_VERSION, SCHEMA_VERSION

EXPECTED_TABLES = {
    "metadata",
    "files",
    "symbols",
    "edges",
    "imports",
    "import_bindings",
    "edge_occurrences",
    "summaries",
    "lexical_index",
    "refresh_leases",
}


def _old_index(path: Path, marker: str = "preserve-me") -> bytes:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            f"""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO metadata VALUES('schema_version', 'csegraph-sqlite-v10');
            CREATE TABLE legacy_payload (value TEXT NOT NULL);
            INSERT INTO legacy_payload VALUES('{marker}');
            """
        )
    return path.read_bytes()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    return repo


def test_old_schema_is_never_migrated(tmp_path: Path):
    db = tmp_path / "old.db"
    _old_index(db)
    index = ProjectIndex(db)
    try:
        with pytest.raises(IndexRequiredError) as exc_info:
            index.initialize_schema()
    finally:
        index.close()

    with sqlite3.connect(db) as conn:
        version = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        marker = conn.execute("SELECT value FROM legacy_payload").fetchone()
    assert version == ("csegraph-sqlite-v10",)
    assert marker == ("preserve-me",)
    assert exc_info.value.to_payload()["next"]["tool"] == "csegraph_index"


def test_index_replaces_old_database_with_valid_v11(tmp_path: Path):
    repo = _repo(tmp_path)
    db = repo / ".csegraph" / "index.db"
    db.parent.mkdir()
    _old_index(db)

    IndexService(db).index(repo)

    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not row[0].startswith(("sqlite_", "lexical_index_"))
        }
        views = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
        version = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        user_version = conn.execute("PRAGMA user_version").fetchone()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()

    assert tables == EXPECTED_TABLES
    assert views == {"entities"}
    assert version == (SCHEMA_VERSION,)
    assert user_version == (SCHEMA_USER_VERSION,)
    assert integrity == ("ok",)
    assert foreign_keys == []
    assert not list(db.parent.glob(f".{db.name}.*.building*"))


def test_failed_rebuild_preserves_old_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = _repo(tmp_path)
    db = repo / ".csegraph" / "index.db"
    db.parent.mkdir()
    before = _old_index(db)

    def fail_write(*_args, **_kwargs):
        raise RuntimeError("injected rebuild failure")

    monkeypatch.setattr("csegraph._core.index.services._write_parsed_files", fail_write)

    with pytest.raises(RuntimeError, match="injected rebuild failure"):
        IndexService(db).index(repo)

    assert db.read_bytes() == before
    assert not list(db.parent.glob(f".{db.name}.*.building*"))
