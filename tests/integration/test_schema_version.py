from __future__ import annotations

import sqlite3

import pytest

from csegraph import IndexRequiredError
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.index.schema import SCHEMA_USER_VERSION, SCHEMA_VERSION


def test_fresh_database_initializes_as_v12(tmp_path):
    db = tmp_path / "index.db"
    index = ProjectIndex(db)
    try:
        index.initialize_schema()
        metadata = index.metadata(raise_if_empty=False)
        user_version = index.conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        index.close()

    assert metadata["schema_version"] == SCHEMA_VERSION
    assert user_version == SCHEMA_USER_VERSION


def test_unversioned_or_future_schema_requires_reindex(tmp_path):
    for name, ddl in (
        ("unversioned.db", "CREATE TABLE nodes (id TEXT PRIMARY KEY);"),
        (
            "future.db",
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO metadata VALUES('schema_version', 'csegraph-sqlite-v999');
            """,
        ),
    ):
        db = tmp_path / name
        with sqlite3.connect(db) as conn:
            conn.executescript(ddl)
        index = ProjectIndex(db)
        try:
            with pytest.raises(IndexRequiredError):
                index.initialize_schema()
        finally:
            index.close()
