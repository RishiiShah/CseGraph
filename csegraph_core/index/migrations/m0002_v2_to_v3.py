"""Migration 0002: csegraph-sqlite-v2 → csegraph-sqlite-v3.

Adds the `is_test` column on `nodes` (backfilled from metadata JSON), adds
`idx_nodes_name` and `idx_nodes_is_test` indexes, and rebuilds `lexical_index`
as a column-weighted FTS5 virtual table (name/path/signature/docstring/summary/
source) so retrieval can score symbol-name matches above docstring matches.
"""
from __future__ import annotations

import sqlite3


FROM_VERSION = "csegraph-sqlite-v2"
TO_VERSION = "csegraph-sqlite-v3"


def upgrade(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("BEGIN")
    try:
        existing_cols = {row["name"] for row in cur.execute("PRAGMA table_info(nodes)")}
        if "is_test" not in existing_cols:
            cur.execute("ALTER TABLE nodes ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0")

        cur.execute(
            """
            UPDATE nodes
               SET is_test = 1
             WHERE metadata IS NOT NULL
               AND json_valid(metadata)
               AND json_extract(metadata, '$.is_test') = 1
            """
        )

        cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(project_id, name)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_is_test ON nodes(project_id, is_test) "
            "WHERE is_test = 1"
        )

        cur.execute("DROP TABLE IF EXISTS lexical_index")
        cur.execute(
            """
            CREATE VIRTUAL TABLE lexical_index USING fts5(
                node_id UNINDEXED,
                name,
                path,
                signature,
                docstring,
                summary,
                source
            )
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
