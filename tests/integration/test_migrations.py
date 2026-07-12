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
    "module_lookup",
    "symbol_lookup",
    "lexical_documents",
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


def test_index_replaces_old_database_with_valid_v12(tmp_path: Path):
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


def test_v12_lexical_documents_triggers_keep_fts_in_sync(tmp_path: Path):
    assert SCHEMA_VERSION == "csegraph-sqlite-v12"
    assert SCHEMA_USER_VERSION == 12
    db = tmp_path / "index.db"
    index = ProjectIndex(db)
    try:
        index.initialize_schema()
        index.conn.execute(
            """
            INSERT INTO lexical_documents(
                node_id, name, path, signature, docstring, summary, source
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            ("symbol:alpha", "alpha", "app.py", "", "", "", ""),
        )
        index.conn.commit()

        inserted = index.conn.execute(
            """
            SELECT node_id FROM lexical_index
            WHERE lexical_index MATCH 'alpha'
            """
        ).fetchall()
        index.conn.execute(
            "UPDATE lexical_documents SET name = 'beta' WHERE node_id = 'symbol:alpha'"
        )
        index.conn.commit()
        stale = index.conn.execute(
            "SELECT node_id FROM lexical_index WHERE lexical_index MATCH 'alpha'"
        ).fetchall()
        updated = index.conn.execute(
            "SELECT node_id FROM lexical_index WHERE lexical_index MATCH 'beta'"
        ).fetchall()
        index.conn.execute("DELETE FROM lexical_documents WHERE node_id = 'symbol:alpha'")
        index.conn.commit()
        deleted = index.conn.execute(
            "SELECT node_id FROM lexical_index WHERE lexical_index MATCH 'beta'"
        ).fetchall()
        triggers = {
            row["name"]
            for row in index.conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'trigger' AND tbl_name = 'lexical_documents'
                """
            )
        }
    finally:
        index.close()

    assert [row["node_id"] for row in inserted] == ["symbol:alpha"]
    assert stale == []
    assert [row["node_id"] for row in updated] == ["symbol:alpha"]
    assert deleted == []
    assert triggers == {
        "lexical_documents_ai",
        "lexical_documents_ad",
        "lexical_documents_au",
    }


def test_bulk_lexical_build_rebuilds_fts_and_restores_triggers(tmp_path: Path):
    db = tmp_path / "index.db"
    index = ProjectIndex(db)
    try:
        index.initialize_schema()
        index.begin_bulk_lexical_write()
        index.conn.execute(
            """
            INSERT INTO lexical_documents(
                node_id, name, path, signature, docstring, summary, source
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            ("symbol:bulk", "bulk_target", "app.py", "", "", "", ""),
        )
        before_rebuild = index.conn.execute(
            """
            SELECT node_id FROM lexical_index
            WHERE lexical_index MATCH 'bulk_target'
            """
        ).fetchall()

        index.finish_bulk_lexical_write()

        after_rebuild = index.conn.execute(
            """
            SELECT node_id FROM lexical_index
            WHERE lexical_index MATCH 'bulk_target'
            """
        ).fetchall()
        triggers = {
            row["name"]
            for row in index.conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'trigger' AND tbl_name = 'lexical_documents'
                """
            )
        }
    finally:
        index.close()

    assert before_rebuild == []
    assert [row["node_id"] for row in after_rebuild] == ["symbol:bulk"]
    assert triggers == {
        "lexical_documents_ai",
        "lexical_documents_ad",
        "lexical_documents_au",
    }


def test_v12_foreign_key_deletes_use_occurrence_indexes(tmp_path: Path):
    db = tmp_path / "index.db"
    index = ProjectIndex(db)
    try:
        index.initialize_schema()
        file_plan = [
            str(row["detail"])
            for row in index.conn.execute(
                "EXPLAIN QUERY PLAN DELETE FROM files WHERE id = ?",
                ("file::app.py",),
            )
        ]
        symbol_plan = [
            str(row["detail"])
            for row in index.conn.execute(
                "EXPLAIN QUERY PLAN DELETE FROM symbols WHERE id = ?",
                ("symbol::app.py::function::run",),
            )
        ]
    finally:
        index.close()

    assert not any("SCAN edge_occurrences" in detail for detail in file_plan)
    assert not any("SCAN edge_occurrences" in detail for detail in symbol_plan)
    assert any("idx_edge_occurrences_source_file" in detail for detail in file_plan)
    assert any("idx_edge_occurrences_enclosing_symbol" in detail for detail in symbol_plan)


def test_bulk_build_defers_and_restores_secondary_indexes(tmp_path: Path):
    db = tmp_path / "index.db"
    index = ProjectIndex(db)
    try:
        index.initialize_schema()
        expected = {
            str(row["name"])
            for row in index.conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'index' AND name GLOB 'idx_*'
                """
            )
        }

        index.begin_bulk_secondary_index_write()

        deferred = {
            str(row["name"])
            for row in index.conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'index' AND name GLOB 'idx_*'
                """
            )
        }
        index.finish_bulk_secondary_index_write()
        restored = {
            str(row["name"])
            for row in index.conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'index' AND name GLOB 'idx_*'
                """
            )
        }
    finally:
        index.close()

    assert expected
    assert deferred == set()
    assert restored == expected


def test_disposable_build_restores_durable_pragmas_before_publish(tmp_path: Path):
    db = tmp_path / "index.db"
    index = ProjectIndex(db)
    try:
        index.begin_disposable_build()
        assert index.conn.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
        assert index.conn.execute("PRAGMA synchronous").fetchone()[0] == 0

        index.initialize_schema()
        index.finish_disposable_build()

        assert index.conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert index.conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert index.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        index.close()


def test_failed_durable_finalization_preserves_old_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo = _repo(tmp_path)
    db = repo / ".csegraph" / "index.db"
    db.parent.mkdir()
    before = _old_index(db)

    def fail_finalization(_index: ProjectIndex) -> None:
        raise RuntimeError("injected durability failure")

    monkeypatch.setattr(
        ProjectIndex,
        "finish_disposable_build",
        fail_finalization,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="injected durability failure"):
        IndexService(db).index(repo)

    assert db.read_bytes() == before
    assert not list(db.parent.glob(f".{db.name}.*.building*"))


def test_failed_rebuild_preserves_old_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = _repo(tmp_path)
    db = repo / ".csegraph" / "index.db"
    db.parent.mkdir()
    before = _old_index(db)

    def fail_write(*_args, **_kwargs):
        raise RuntimeError("injected rebuild failure")

    monkeypatch.setattr("csegraph._core.index.writer._write_parsed_files", fail_write)

    with pytest.raises(RuntimeError, match="injected rebuild failure"):
        IndexService(db).index(repo)

    assert db.read_bytes() == before
    assert not list(db.parent.glob(f".{db.name}.*.building*"))
