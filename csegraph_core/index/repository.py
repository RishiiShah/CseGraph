from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from csegraph_core.core.errors import UnsupportedSchemaError
from csegraph_core.core.ids import file_node_id
from csegraph_core.index.schema import (
    METADATA_UPSERT,
    SCHEMA_DDL,
    SCHEMA_USER_VERSION,
    SCHEMA_VERSION,
)


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
        ):
            self.conn.execute(pragma)

    def close(self) -> None:
        self.conn.close()

    def initialize_schema(self) -> None:
        existing_version = self._existing_schema_version()
        if existing_version is None and self._has_csegraph_objects():
            raise UnsupportedSchemaError()
        if existing_version is not None and existing_version != SCHEMA_VERSION:
            raise UnsupportedSchemaError()

        cur = self.conn.cursor()
        cur.executescript(SCHEMA_DDL)
        cur.execute(METADATA_UPSERT, (SCHEMA_VERSION,))
        cur.execute(f"PRAGMA user_version = {SCHEMA_USER_VERSION}")
        self.conn.commit()

    def _existing_schema_version(self) -> Optional[str]:
        if self._table_exists("metadata"):
            row = self.conn.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
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

    def set_metadata(self, root_dir: str, profile: str) -> None:
        now = time.time()
        existing = self.metadata(raise_if_empty=False)
        created_at = existing.get("created_at", str(now))
        rows = {
            "schema_version": SCHEMA_VERSION,
            "root_dir": root_dir,
            "active_profile": profile,
            "created_at": created_at,
            "updated_at": str(now),
        }
        self.conn.executemany(
            """
            INSERT INTO metadata(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            sorted(rows.items()),
        )
        self.conn.commit()

    def metadata(self, *, raise_if_empty: bool = True) -> Dict[str, str]:
        if not self._table_exists("metadata"):
            if raise_if_empty:
                raise ValueError("No project is indexed in this database. Run csegraph index first.")
            return {}
        values = {
            row["key"]: row["value"]
            for row in self.conn.execute("SELECT key, value FROM metadata")
        }
        if raise_if_empty and "root_dir" not in values:
            raise ValueError("No project is indexed in this database. Run csegraph index first.")
        return values

    def clear_graph(self) -> None:
        self.conn.execute("DELETE FROM retrieval_context")
        self.conn.execute("DELETE FROM retrieval_runs")
        self.conn.execute("DELETE FROM lexical_index")
        self.conn.execute("DELETE FROM summaries")
        self.conn.execute("DELETE FROM embedding_cache")
        self.conn.execute("DELETE FROM edges")
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
        if remove_incoming:
            self.conn.execute(
                f"DELETE FROM edges WHERE target IN ({placeholders})",
                node_ids,
            )
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
        return int(cur.lastrowid)

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


def json_dumps(value: Optional[Dict[str, Any]]) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)


def json_loads(value: Optional[str]) -> Dict[str, Any]:
    if not value:
        return {}
    return json.loads(value)
