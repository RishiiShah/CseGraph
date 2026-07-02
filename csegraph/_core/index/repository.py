from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from csegraph._core.core.errors import UnsupportedSchemaError
from csegraph._core.core.ids import file_node_id
from csegraph._core.index.schema import (
    METADATA_UPSERT,
    SCHEMA_DDL,
    SCHEMA_USER_VERSION,
    SCHEMA_VERSION,
)
from csegraph._core.repo_state import git_head_state


class ProjectIndex:
    """SQLite boundary for one csegraph repository index."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        for pragma in (
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
            "PRAGMA foreign_keys = ON",
            "PRAGMA temp_store = MEMORY",
            "PRAGMA cache_size = -64000",
            "PRAGMA busy_timeout = 5000",
        ):
            self.conn.execute(pragma)

    def close(self) -> None:
        self.conn.close()

    def initialize_schema(self, *, reset_on_unsupported: bool = False) -> None:
        existing_version = self._existing_schema_version()
        if existing_version is None and self._has_csegraph_objects():
            if not reset_on_unsupported:
                raise UnsupportedSchemaError()
            self._drop_csegraph_objects()
            existing_version = None
        if existing_version is not None and existing_version != SCHEMA_VERSION:
            if not reset_on_unsupported:
                raise UnsupportedSchemaError()
            self._drop_csegraph_objects()
            existing_version = None

        cur = self.conn.cursor()
        cur.executescript(SCHEMA_DDL)
        cur.execute(METADATA_UPSERT, (SCHEMA_VERSION,))
        cur.execute(f"PRAGMA user_version = {SCHEMA_USER_VERSION}")
        self.conn.commit()

    def _drop_csegraph_objects(self) -> None:
        for name in (
            "lexical_index",
            "retrieval_context",
            "retrieval_runs",
            "embedding_cache",
            "summaries",
            "test_assertions",
            "edge_occurrences",
            "import_bindings",
            "symbol_history",
            "symbol_references",
            "imports",
            "relationships",
            "edges",
            "symbols",
            "files",
            "nodes",
            "metadata",
            "schema_meta",
            "projects",
        ):
            self.conn.execute(f"DROP TABLE IF EXISTS {name}")
        self.conn.commit()

    def _existing_schema_version(self) -> Optional[str]:
        for table_name in ("metadata", "schema_meta"):
            version = self._schema_version_from_table(table_name)
            if version is not None:
                return version

        return None

    def _schema_version_from_table(self, table_name: str) -> Optional[str]:
        if self._table_exists(table_name):
            row = self.conn.execute(
                f"SELECT value FROM {table_name} WHERE key = 'schema_version'"
            ).fetchone()
            return row["value"] if row else None

        return None

    def _table_exists(self, name: str) -> bool:
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (name,),
        ).fetchone()
        return row is not None

    def _has_csegraph_objects(self) -> bool:
        row = self.conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
              AND name NOT LIKE 'lexical_index_%'
            LIMIT 1
            """
        ).fetchone()
        return row is not None

    def set_metadata(
        self,
        root_dir: str,
        profile: str,
        include_roots: Optional[Sequence[str]] = None,
    ) -> None:
        now = time.time()
        existing = self.metadata(raise_if_empty=False)
        created_at = existing.get("created_at", str(now))
        branch, commit = git_head_state(root_dir)
        rows = {
            "schema_version": SCHEMA_VERSION,
            "root_dir": root_dir,
            "active_profile": profile,
            "created_at": created_at,
            "updated_at": str(now),
            "built_branch": branch or "",
            "built_commit": commit or "",
            "index_revision": existing.get("index_revision", "0"),
        }
        if include_roots is not None:
            rows["include_roots"] = json.dumps(list(include_roots), sort_keys=True)
        self.conn.executemany(
            """
            INSERT INTO metadata(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            sorted(rows.items()),
        )
        self.conn.commit()

    def index_revision(self) -> int:
        metadata = self.metadata(raise_if_empty=False)
        try:
            return max(0, int(metadata.get("index_revision", "0")))
        except (TypeError, ValueError):
            return 0

    def bump_index_revision(self) -> int:
        revision = self.index_revision() + 1
        self.conn.execute(
            """
            INSERT INTO metadata(key, value)
            VALUES('index_revision', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(revision),),
        )
        self.conn.commit()
        return revision

    def metadata(self, *, raise_if_empty: bool = True) -> Dict[str, str]:
        if not self._table_exists("metadata"):
            if raise_if_empty:
                raise ValueError(
                    "No project is indexed in this database. Run csegraph index first."
                )
            return {}
        values = {
            row["key"]: row["value"] for row in self.conn.execute("SELECT key, value FROM metadata")
        }
        if raise_if_empty and "root_dir" not in values:
            raise ValueError("No project is indexed in this database. Run csegraph index first.")
        return values

    def clear_graph(self) -> None:
        self.conn.execute("DELETE FROM retrieval_context")
        self.conn.execute("DELETE FROM retrieval_runs")
        self.conn.execute("DELETE FROM retrieval_plan_cache")
        self.conn.execute("DELETE FROM lexical_index")
        self.conn.execute("DELETE FROM summaries")
        self.conn.execute("DELETE FROM embedding_cache")
        self.conn.execute("DELETE FROM symbol_references")
        self.conn.execute("DELETE FROM imports")
        self.conn.execute("DELETE FROM relationships")
        self.conn.execute("DELETE FROM edges")
        self.conn.execute("DELETE FROM symbols")
        self.conn.execute("DELETE FROM files")
        self.conn.execute("DELETE FROM nodes")
        self.conn.commit()

    def delete_file_payload(self, rel_path: str, remove_incoming: bool) -> List[str]:
        file_id = file_node_id(rel_path)
        file_row = self.conn.execute(
            "SELECT id FROM nodes WHERE id = ?",
            (file_id,),
        ).fetchone()
        if file_row is None:
            return []

        symbol_ids = [
            row["id"]
            for row in self.conn.execute(
                """
                SELECT id FROM nodes
                WHERE path = ?
                  AND type IN ('class','function','method','test')
                """,
                (rel_path,),
            )
        ]
        node_ids = [file_id, *symbol_ids]
        placeholders = ",".join("?" for _ in node_ids)

        self.conn.execute(
            f"DELETE FROM edges WHERE source IN ({placeholders})",
            node_ids,
        )
        self.conn.execute(
            f"DELETE FROM relationships WHERE source IN ({placeholders})",
            node_ids,
        )
        if remove_incoming:
            self.conn.execute(
                f"DELETE FROM edges WHERE target IN ({placeholders})",
                node_ids,
            )
            self.conn.execute(
                f"DELETE FROM relationships WHERE target IN ({placeholders})",
                node_ids,
            )
        self.conn.execute(
            f"DELETE FROM symbol_references WHERE source_file_id = ? OR enclosing_symbol_id IN ({placeholders})",
            [file_id, *node_ids],
        )
        self.conn.execute("DELETE FROM imports WHERE file_id = ?", (file_id,))
        self.conn.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))
        self.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        self.conn.execute(
            f"DELETE FROM lexical_index WHERE node_id IN ({placeholders})",
            node_ids,
        )
        self.conn.execute(
            f"DELETE FROM summaries WHERE node_id IN ({placeholders})",
            node_ids,
        )
        self.conn.execute(
            f"DELETE FROM embedding_cache WHERE node_id IN ({placeholders})",
            node_ids,
        )
        self.conn.execute(
            f"DELETE FROM nodes WHERE id IN ({placeholders})",
            node_ids,
        )
        self.conn.commit()
        return symbol_ids

    def cleanup_orphan_edges(self) -> None:
        self.conn.execute(
            """
            DELETE FROM edges
             WHERE source NOT IN (SELECT id FROM nodes)
                OR target NOT IN (SELECT id FROM nodes)
            """
        )
        self.conn.execute(
            """
            DELETE FROM relationships
             WHERE source NOT IN (SELECT id FROM nodes)
                OR target NOT IN (SELECT id FROM nodes)
            """
        )
        self.conn.commit()

    def cleanup_orphan_folders(self) -> None:
        """Drop folder nodes that no longer have descendants."""
        while True:
            removed = self.conn.execute(
                """
                DELETE FROM nodes
                WHERE type = 'folder'
                  AND id NOT IN (SELECT parent_id FROM nodes WHERE parent_id IS NOT NULL)
                """
            )
            if removed.rowcount == 0:
                break
        self.conn.commit()

    def insert_retrieval_run(
        self,
        query: str,
        target: str,
        profile: str,
        metrics: Dict[str, float],
        sufficient: bool,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO retrieval_runs(
                query, target, profile,
                dependency_completeness, entity_coverage, semantic_overlap,
                model_confidence, sufficient, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                query,
                target,
                profile,
                metrics["dependency_completeness"],
                metrics["entity_coverage"],
                metrics["semantic_overlap"],
                metrics["model_confidence"],
                1 if sufficient else 0,
                time.time(),
            ),
        )
        self.conn.commit()
        row_id = cur.lastrowid
        if row_id is None:
            raise RuntimeError("SQLite did not return a retrieval run id")
        return int(row_id)

    def insert_retrieval_context(
        self,
        run_id: int,
        rows: Iterable[Dict[str, Any]],
    ) -> None:
        params = [
            (
                run_id,
                row["node_id"],
                row["rank"],
                row["score"],
                1 if row["raw_code"] else 0,
                json.dumps(row["evidence"], sort_keys=True),
            )
            for row in rows
        ]
        if params:
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO retrieval_context(
                    run_id, node_id, rank, score, raw_code, evidence
                )
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                params,
            )
            self.conn.commit()


def json_dumps(value: Optional[Dict[str, Any]]) -> str:
    if value is None:
        return "{}"
    return json.dumps(value, sort_keys=True)


def json_loads(value: Optional[str]) -> Dict[str, Any]:
    if not value:
        return {}
    return json.loads(value)
