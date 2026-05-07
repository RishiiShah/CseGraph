"""Migration 0001: csegraph-sqlite-v1 → csegraph-sqlite-v2.

Collapses the v1 `files` and `symbols` tables into a unified `nodes` table,
synthesizes repo and folder nodes for parent_id chains, and renames
edges.source_id/target_id → source_node_id/target_node_id. All existing
node IDs are preserved.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from csegraph_core.core.ids import file_node_id, folder_node_id, repo_node_id


FROM_VERSION = "csegraph-sqlite-v1"
TO_VERSION = "csegraph-sqlite-v2"


def upgrade(conn: sqlite3.Connection) -> None:
    now = time.time()
    cur = conn.cursor()
    cur.execute("BEGIN")
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL,
                parent_id TEXT,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                language TEXT,
                sha256 TEXT,
                signature TEXT,
                docstring TEXT,
                start_line INTEGER,
                end_line INTEGER,
                source_hash TEXT NOT NULL,
                parse_status TEXT,
                parse_error TEXT,
                metadata TEXT,
                updated_at REAL NOT NULL
            )
            """
        )

        for project in cur.execute("SELECT id, root_dir FROM projects").fetchall():
            project_id = int(project["id"])
            root_name = Path(project["root_dir"]).name or "repo"
            repo_id = repo_node_id(root_name)
            cur.execute(
                """
                INSERT OR IGNORE INTO nodes(
                    id, project_id, parent_id, type, name, path,
                    language, sha256, signature, docstring,
                    start_line, end_line, source_hash,
                    parse_status, parse_error, metadata, updated_at
                ) VALUES(?, ?, NULL, 'repo', ?, '', NULL, NULL, NULL, NULL,
                         NULL, NULL, '', NULL, NULL, NULL, ?)
                """,
                (repo_id, project_id, root_name, now),
            )

            file_rows = cur.execute(
                """
                SELECT path, language, sha256, parse_status, parse_error, size, mtime
                FROM files WHERE project_id = ?
                """,
                (project_id,),
            ).fetchall()

            folder_paths: set[str] = set()
            for file_row in file_rows:
                parts = file_row["path"].split("/")[:-1]
                for i in range(1, len(parts) + 1):
                    folder_paths.add("/".join(parts[:i]))

            for rel_dir in sorted(folder_paths, key=lambda p: p.count("/")):
                parent_parts = rel_dir.split("/")[:-1]
                parent = folder_node_id("/".join(parent_parts)) if parent_parts else repo_id
                cur.execute(
                    """
                    INSERT OR IGNORE INTO nodes(
                        id, project_id, parent_id, type, name, path,
                        language, sha256, signature, docstring,
                        start_line, end_line, source_hash,
                        parse_status, parse_error, metadata, updated_at
                    ) VALUES(?, ?, ?, 'folder', ?, ?, NULL, NULL, NULL, NULL,
                             NULL, NULL, '', NULL, NULL, NULL, ?)
                    """,
                    (
                        folder_node_id(rel_dir),
                        project_id,
                        parent,
                        rel_dir.rsplit("/", 1)[-1],
                        rel_dir,
                        now,
                    ),
                )

            for file_row in file_rows:
                rel_path = file_row["path"]
                parent_dir = "/".join(rel_path.split("/")[:-1])
                parent = folder_node_id(parent_dir) if parent_dir else repo_id
                metadata = json.dumps(
                    {"size": file_row["size"], "mtime": file_row["mtime"]},
                    sort_keys=True,
                )
                cur.execute(
                    """
                    INSERT OR IGNORE INTO nodes(
                        id, project_id, parent_id, type, name, path,
                        language, sha256, signature, docstring,
                        start_line, end_line, source_hash,
                        parse_status, parse_error, metadata, updated_at
                    ) VALUES(?, ?, ?, 'file', ?, ?, ?, ?, NULL, NULL,
                             NULL, NULL, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_node_id(rel_path),
                        project_id,
                        parent,
                        Path(rel_path).name,
                        rel_path,
                        file_row["language"],
                        file_row["sha256"],
                        file_row["sha256"],
                        file_row["parse_status"],
                        file_row["parse_error"],
                        metadata,
                        now,
                    ),
                )

            for sym_row in cur.execute(
                """
                SELECT s.id, s.kind, s.name, s.parent_symbol_id,
                       s.signature, s.docstring, s.start_line, s.end_line,
                       s.source_hash, f.path AS rel_path
                FROM symbols s JOIN files f ON f.id = s.file_id
                WHERE s.project_id = ?
                """,
                (project_id,),
            ).fetchall():
                parent = sym_row["parent_symbol_id"] or file_node_id(sym_row["rel_path"])
                cur.execute(
                    """
                    INSERT OR IGNORE INTO nodes(
                        id, project_id, parent_id, type, name, path,
                        language, sha256, signature, docstring,
                        start_line, end_line, source_hash,
                        parse_status, parse_error, metadata, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)
                    """,
                    (
                        sym_row["id"],
                        project_id,
                        parent,
                        sym_row["kind"],
                        sym_row["name"],
                        sym_row["rel_path"],
                        sym_row["signature"],
                        sym_row["docstring"],
                        sym_row["start_line"],
                        sym_row["end_line"],
                        sym_row["source_hash"],
                        now,
                    ),
                )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS edges_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                source_node_id TEXT NOT NULL,
                target_node_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                metadata TEXT,
                UNIQUE(project_id, source_node_id, target_node_id, relation, metadata)
            )
            """
        )
        cur.execute(
            """
            INSERT OR IGNORE INTO edges_new(project_id, source_node_id, target_node_id, relation, metadata)
            SELECT project_id, source_id, target_id, relation, metadata FROM edges
            """
        )
        cur.execute("DROP TABLE edges")
        cur.execute("ALTER TABLE edges_new RENAME TO edges")
        cur.execute("DROP TABLE IF EXISTS symbols")
        cur.execute("DROP TABLE IF EXISTS files")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
