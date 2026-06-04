import sqlite3

import pytest

from csegraph._core.core.errors import UnsupportedSchemaError
from csegraph._core.index.repository import ProjectIndex


def test_old_schema_version_is_rejected(tmp_path):
    db_path = tmp_path / "old.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES('schema_version', 'csegraph-sqlite-v1');
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                root_dir TEXT NOT NULL UNIQUE,
                active_profile TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )

    idx = ProjectIndex(db_path)
    try:
        with pytest.raises(UnsupportedSchemaError) as exc_info:
            idx.initialize_schema()
    finally:
        idx.close()

    assert exc_info.value.error_code == "unsupported_schema"
