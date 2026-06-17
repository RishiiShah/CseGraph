import sqlite3

from csegraph._core.index.repository import ProjectIndex
from csegraph._core.index.schema import SCHEMA_USER_VERSION, SCHEMA_VERSION


def test_old_schema_version_is_migrated(tmp_path):
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
            INSERT INTO projects(root_dir, active_profile, created_at, updated_at)
            VALUES('/tmp/example', 'small', 10.0, 20.0);
            """
        )

    idx = ProjectIndex(db_path)
    try:
        idx.initialize_schema()
        metadata = idx.metadata(raise_if_empty=False)
    finally:
        idx.close()

    assert metadata["schema_version"] == SCHEMA_VERSION
    assert metadata["root_dir"] == "/tmp/example"
    assert metadata["active_profile"] == "small"
    assert metadata["created_at"] == "10.0"
    assert metadata["updated_at"] == "20.0"

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            )
        }
        node_columns = {row[1] for row in conn.execute("PRAGMA table_info(nodes)")}
        edge_columns = {row[1] for row in conn.execute("PRAGMA table_info(edges)")}
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]

    assert "schema_meta" not in tables
    assert "metadata" in tables
    assert "nodes" in tables
    assert "edges" in tables
    assert "lexical_index" in tables
    assert {"source_hash", "is_test", "community_id", "updated_at"}.issubset(node_columns)
    assert {"confidence", "confidence_tier"}.issubset(edge_columns)
    assert user_version == SCHEMA_USER_VERSION


def test_partial_legacy_tables_gain_current_columns(tmp_path):
    db_path = tmp_path / "partial.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO metadata(key, value) VALUES('schema_version', 'csegraph-sqlite-v4');
            CREATE TABLE nodes (id TEXT PRIMARY KEY);
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                relation TEXT NOT NULL
            );
            """
        )

    idx = ProjectIndex(db_path)
    try:
        idx.initialize_schema()
        metadata = idx.metadata(raise_if_empty=False)
    finally:
        idx.close()

    with sqlite3.connect(db_path) as conn:
        node_columns = {row[1] for row in conn.execute("PRAGMA table_info(nodes)")}
        edge_columns = {row[1] for row in conn.execute("PRAGMA table_info(edges)")}

    assert metadata["schema_version"] == SCHEMA_VERSION
    assert {"type", "name", "path", "language", "source_hash", "is_test"}.issubset(
        node_columns
    )
    assert {"metadata", "confidence", "confidence_tier"}.issubset(edge_columns)
