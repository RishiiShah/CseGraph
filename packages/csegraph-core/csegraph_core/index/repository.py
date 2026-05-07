from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from csegraph.index.schema import SCHEMA_DDL, SCHEMA_META_UPSERT, SCHEMA_VERSION


class ProjectIndex:
    """Thin SQLite boundary for csegraph project indexes."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def initialize_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(SCHEMA_DDL)
        cur.execute(SCHEMA_META_UPSERT, (SCHEMA_VERSION,))
        self.conn.commit()

    def upsert_project(self, root_dir: str, profile: str) -> int:
        now = time.time()
        self.conn.execute(
            """
            INSERT INTO projects(root_dir, active_profile, created_at, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(root_dir) DO UPDATE SET
                active_profile = excluded.active_profile,
                updated_at = excluded.updated_at
            """,
            (root_dir, profile, now, now),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM projects WHERE root_dir = ?", (root_dir,)
        ).fetchone()
        return int(row["id"])

    def get_project(self) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM projects ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            raise ValueError("No project is indexed in this database. Run csegraph index first.")
        return row

    def clear_project_graph(self, project_id: int) -> None:
        self.conn.execute("DELETE FROM retrieval_context WHERE run_id IN (SELECT id FROM retrieval_runs WHERE project_id = ?)", (project_id,))
        self.conn.execute("DELETE FROM retrieval_runs WHERE project_id = ?", (project_id,))
        self.conn.execute("DELETE FROM lexical_index")
        self.conn.execute("DELETE FROM edges WHERE project_id = ?", (project_id,))
        self.conn.execute("DELETE FROM symbols WHERE project_id = ?", (project_id,))
        self.conn.execute("DELETE FROM files WHERE project_id = ?", (project_id,))
        self.conn.commit()

    def delete_file_payload(self, project_id: int, rel_path: str, remove_incoming: bool) -> List[str]:
        file_row = self.conn.execute(
            "SELECT id FROM files WHERE project_id = ? AND path = ?",
            (project_id, rel_path),
        ).fetchone()
        if file_row is None:
            return []

        file_id = int(file_row["id"])
        symbol_ids = [
            row["id"]
            for row in self.conn.execute(
                "SELECT id FROM symbols WHERE project_id = ? AND file_id = ?",
                (project_id, file_id),
            )
        ]
        node_ids = [f"file::{rel_path}", *symbol_ids]

        placeholders = ",".join("?" for _ in node_ids)
        if node_ids:
            params: Sequence[Any] = (project_id, *node_ids)
            self.conn.execute(
                f"DELETE FROM edges WHERE project_id = ? AND source_id IN ({placeholders})",
                params,
            )
            if remove_incoming:
                self.conn.execute(
                    f"DELETE FROM edges WHERE project_id = ? AND target_id IN ({placeholders})",
                    params,
                )
            for node_id in node_ids:
                self.conn.execute("DELETE FROM lexical_index WHERE node_id = ?", (node_id,))

        self.conn.execute(
            "DELETE FROM symbols WHERE project_id = ? AND file_id = ?",
            (project_id, file_id),
        )
        self.conn.execute(
            "DELETE FROM files WHERE project_id = ? AND id = ?",
            (project_id, file_id),
        )
        self.conn.commit()
        return symbol_ids

    def cleanup_orphan_edges(self, project_id: int) -> None:
        valid_ids = set(self.file_node_ids(project_id)) | set(self.symbol_ids(project_id))
        for row in self.conn.execute(
            "SELECT id, source_id, target_id FROM edges WHERE project_id = ?",
            (project_id,),
        ):
            if row["source_id"] not in valid_ids or row["target_id"] not in valid_ids:
                self.conn.execute("DELETE FROM edges WHERE id = ?", (row["id"],))
        self.conn.commit()

    def file_node_ids(self, project_id: int) -> List[str]:
        return [
            f"file::{row['path']}"
            for row in self.conn.execute(
                "SELECT path FROM files WHERE project_id = ?", (project_id,)
            )
        ]

    def symbol_ids(self, project_id: int) -> List[str]:
        return [
            row["id"]
            for row in self.conn.execute(
                "SELECT id FROM symbols WHERE project_id = ?", (project_id,)
            )
        ]

    def insert_retrieval_run(
        self,
        project_id: int,
        query_text: str,
        target_node_id: str,
        profile: str,
        metrics: Dict[str, float],
        is_sufficient: bool,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO retrieval_runs(
                project_id, query_text, target_node_id, profile,
                dependency_completeness, entity_coverage, semantic_overlap,
                model_confidence, is_sufficient, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                query_text,
                target_node_id,
                profile,
                metrics["dependency_completeness"],
                metrics["entity_coverage"],
                metrics["semantic_overlap"],
                metrics["model_confidence"],
                1 if is_sufficient else 0,
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
        for row in rows:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO retrieval_context(
                    run_id, node_id, rank, score, raw_code, evidence
                )
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    row["node_id"],
                    row["rank"],
                    row["score"],
                    1 if row["raw_code"] else 0,
                    json.dumps(row["evidence"], sort_keys=True),
                ),
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
