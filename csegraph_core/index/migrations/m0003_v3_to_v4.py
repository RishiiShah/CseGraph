"""Migration 0003: csegraph-sqlite-v3 → csegraph-sqlite-v4.

Adds NOT NULL to nodes.language via the SQLite table-rebuild pattern (SQLite
does not support ALTER COLUMN). Steps:

1. Backfill any remaining NULLs to 'python'.
2. Create nodes_v4 with the v4 DDL (language TEXT NOT NULL).
3. Copy all rows from nodes into nodes_v4.
4. Drop the old nodes table, rename nodes_v4 → nodes.
5. Recreate all five nodes indices.

After this migration `PRAGMA table_info(nodes)` reports notnull=1 for language.
"""
from __future__ import annotations

import sqlite3

FROM_VERSION = "csegraph-sqlite-v3"
TO_VERSION = "csegraph-sqlite-v4"

_NODES_V4_DDL = """
CREATE TABLE nodes_v4 (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL,
    parent_id TEXT,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    sha256 TEXT,
    signature TEXT,
    docstring TEXT,
    start_line INTEGER,
    end_line INTEGER,
    source_hash TEXT NOT NULL,
    parse_status TEXT,
    parse_error TEXT,
    metadata TEXT,
    is_test INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
)
"""


def upgrade(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("BEGIN")
    try:
        cur.execute("UPDATE nodes SET language = 'python' WHERE language IS NULL")
        cur.execute("UPDATE nodes SET language = 'non_code' WHERE language = ''")
        cur.execute(_NODES_V4_DDL)
        cur.execute(
            "INSERT INTO nodes_v4("
            "id, project_id, parent_id, type, name, path, language, sha256, signature,"
            " docstring, start_line, end_line, source_hash, parse_status, parse_error,"
            " metadata, is_test, updated_at"
            ") SELECT"
            " id, project_id, parent_id, type, name, path, language, sha256, signature,"
            " docstring, start_line, end_line, source_hash, parse_status, parse_error,"
            " metadata, is_test, updated_at"
            " FROM nodes"
        )
        cur.execute("DROP TABLE nodes")
        cur.execute("ALTER TABLE nodes_v4 RENAME TO nodes")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_project_type_name"
            " ON nodes(project_id, type, name)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_path ON nodes(project_id, path)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(project_id, name)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_is_test"
            " ON nodes(project_id, is_test) WHERE is_test = 1"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
