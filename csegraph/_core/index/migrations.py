from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

from csegraph._core.core.errors import UnsupportedSchemaError
from csegraph._core.index.schema import (
    METADATA_UPSERT,
    SCHEMA_DDL,
    SCHEMA_USER_VERSION,
    SCHEMA_VERSION,
)


@dataclass(frozen=True)
class SchemaMigration:
    source_version: str
    target_version: str
    apply: Callable[[sqlite3.Connection], None]


_CURRENT_METADATA_DDL = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

_NODE_COLUMN_DEFINITIONS = {
    "parent_id": "parent_id TEXT",
    "type": "type TEXT NOT NULL DEFAULT 'unknown'",
    "name": "name TEXT NOT NULL DEFAULT ''",
    "path": "path TEXT NOT NULL DEFAULT ''",
    "language": "language TEXT NOT NULL DEFAULT ''",
    "sha256": "sha256 TEXT",
    "signature": "signature TEXT",
    "docstring": "docstring TEXT",
    "start_line": "start_line INTEGER",
    "end_line": "end_line INTEGER",
    "source_hash": "source_hash TEXT NOT NULL DEFAULT ''",
    "parse_status": "parse_status TEXT",
    "parse_error": "parse_error TEXT",
    "metadata": "metadata TEXT",
    "is_test": "is_test INTEGER NOT NULL DEFAULT 0",
    "community_id": "community_id INTEGER",
    "updated_at": "updated_at REAL NOT NULL DEFAULT 0",
}

_EDGE_COLUMN_DEFINITIONS = {
    "source": "source TEXT NOT NULL DEFAULT ''",
    "target": "target TEXT NOT NULL DEFAULT ''",
    "relation": "relation TEXT NOT NULL DEFAULT ''",
    "metadata": "metadata TEXT",
    "confidence": "confidence REAL NOT NULL DEFAULT 1.0",
    "confidence_tier": "confidence_tier TEXT NOT NULL DEFAULT 'EXTRACTED'",
}

_SUMMARY_COLUMN_DEFINITIONS = {
    "source_hash": "source_hash TEXT NOT NULL DEFAULT ''",
    "summary": "summary TEXT NOT NULL DEFAULT ''",
    "kind": "kind TEXT NOT NULL DEFAULT ''",
    "updated_at": "updated_at REAL NOT NULL DEFAULT 0",
}

_EMBEDDING_COLUMN_DEFINITIONS = {
    "model": "model TEXT NOT NULL DEFAULT ''",
    "source_hash": "source_hash TEXT NOT NULL DEFAULT ''",
    "vector": "vector BLOB NOT NULL DEFAULT X''",
    "updated_at": "updated_at REAL NOT NULL DEFAULT 0",
}

_RETRIEVAL_RUN_COLUMN_DEFINITIONS = {
    "query": "query TEXT NOT NULL DEFAULT ''",
    "target": "target TEXT",
    "profile": "profile TEXT NOT NULL DEFAULT ''",
    "dependency_completeness": "dependency_completeness REAL NOT NULL DEFAULT 0",
    "entity_coverage": "entity_coverage REAL NOT NULL DEFAULT 0",
    "semantic_overlap": "semantic_overlap REAL NOT NULL DEFAULT 0",
    "model_confidence": "model_confidence REAL NOT NULL DEFAULT 0",
    "sufficient": "sufficient INTEGER NOT NULL DEFAULT 0",
    "created_at": "created_at REAL NOT NULL DEFAULT 0",
}

_RETRIEVAL_CONTEXT_COLUMN_DEFINITIONS = {
    "run_id": "run_id INTEGER NOT NULL DEFAULT 0",
    "node_id": "node_id TEXT NOT NULL DEFAULT ''",
    "rank": "rank INTEGER NOT NULL DEFAULT 0",
    "score": "score REAL NOT NULL DEFAULT 0",
    "raw_code": "raw_code INTEGER NOT NULL DEFAULT 0",
    "evidence": "evidence TEXT NOT NULL DEFAULT '{}'",
}

_LEXICAL_COLUMNS = {
    "node_id",
    "name",
    "path",
    "signature",
    "docstring",
    "summary",
    "source",
}


def migrate_schema(conn: sqlite3.Connection, existing_version: str) -> None:
    """Apply known SQLite schema migrations up to the current schema version."""
    version = existing_version
    while version != SCHEMA_VERSION:
        migration = SCHEMA_MIGRATIONS.get(version)
        if migration is None:
            raise UnsupportedSchemaError()
        migration.apply(conn)
        version = migration.target_version

    conn.execute(METADATA_UPSERT, (SCHEMA_VERSION,))
    conn.execute(f"PRAGMA user_version = {SCHEMA_USER_VERSION}")


def _migrate_legacy_to_current(conn: sqlite3.Connection) -> None:
    conn.execute(_CURRENT_METADATA_DDL)
    _copy_schema_meta(conn)
    _copy_legacy_project_metadata(conn)
    _ensure_current_columns(conn)
    _ensure_lexical_index_shape(conn)
    conn.executescript(SCHEMA_DDL)
    _ensure_current_columns(conn)
    conn.execute("DROP TABLE IF EXISTS schema_meta")


def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    """Add impact evidence tables without rewriting the v7 graph."""
    conn.executescript(SCHEMA_DDL)


def _copy_schema_meta(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "schema_meta"):
        return
    rows = conn.execute("SELECT key, value FROM schema_meta WHERE key != 'schema_version'")
    conn.executemany(
        """
        INSERT OR IGNORE INTO metadata(key, value)
        VALUES(?, ?)
        """,
        rows,
    )


def _copy_legacy_project_metadata(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "projects"):
        return
    columns = _table_columns(conn, "projects")
    if not {"root_dir", "active_profile", "created_at", "updated_at"}.issubset(columns):
        return

    row = conn.execute(
        """
        SELECT root_dir, active_profile, created_at, updated_at
        FROM projects
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return

    conn.executemany(
        """
        INSERT OR IGNORE INTO metadata(key, value)
        VALUES(?, ?)
        """,
        (
            ("root_dir", str(row[0])),
            ("active_profile", str(row[1])),
            ("created_at", str(row[2])),
            ("updated_at", str(row[3])),
        ),
    )


def _ensure_current_columns(conn: sqlite3.Connection) -> None:
    _ensure_table_columns(conn, "nodes", _NODE_COLUMN_DEFINITIONS)
    _ensure_table_columns(conn, "edges", _EDGE_COLUMN_DEFINITIONS)
    _ensure_table_columns(conn, "summaries", _SUMMARY_COLUMN_DEFINITIONS)
    _ensure_table_columns(conn, "embedding_cache", _EMBEDDING_COLUMN_DEFINITIONS)
    _ensure_table_columns(conn, "retrieval_runs", _RETRIEVAL_RUN_COLUMN_DEFINITIONS)
    _ensure_table_columns(conn, "retrieval_context", _RETRIEVAL_CONTEXT_COLUMN_DEFINITIONS)


def _ensure_table_columns(
    conn: sqlite3.Connection,
    table_name: str,
    column_definitions: dict[str, str],
) -> None:
    if not _table_exists(conn, table_name):
        return
    columns = _table_columns(conn, table_name)
    for column_name, definition in column_definitions.items():
        if column_name not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {definition}")


def _ensure_lexical_index_shape(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "lexical_index"):
        columns = _table_columns(conn, "lexical_index")
        if columns != _LEXICAL_COLUMNS:
            conn.execute("DROP TABLE lexical_index")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({name})")}


SCHEMA_MIGRATIONS = {
    version: SchemaMigration(version, SCHEMA_VERSION, _migrate_legacy_to_current)
    for version in (
        "csegraph-sqlite-v1",
        "csegraph-sqlite-v2",
        "csegraph-sqlite-v3",
        "csegraph-sqlite-v4",
        "csegraph-sqlite-v5",
        "csegraph-sqlite-v6",
    )
}
SCHEMA_MIGRATIONS["csegraph-sqlite-v7"] = SchemaMigration(
    "csegraph-sqlite-v7",
    SCHEMA_VERSION,
    _migrate_v7_to_v8,
)
