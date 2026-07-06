from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence

from csegraph._core.core.errors import IndexRequiredError
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
            "PRAGMA busy_timeout = 5000",
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
            "PRAGMA foreign_keys = ON",
            "PRAGMA temp_store = MEMORY",
            "PRAGMA cache_size = -64000",
        ):
            deadline = time.monotonic() + 5.0
            while True:
                try:
                    self.conn.execute(pragma)
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                        self.conn.close()
                        raise
                    time.sleep(0.01)
        self._in_transaction = False

    def close(self) -> None:
        self.conn.close()

    @contextlib.contextmanager
    def atomic_write(self) -> Iterator[None]:
        if self._in_transaction:
            yield
            return

        # Commit any implicitly started transaction by Python's DB-API
        self.conn.commit()
        self.conn.execute("BEGIN IMMEDIATE")
        self._in_transaction = True
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self._in_transaction = False

    def _commit(self) -> None:
        if not self._in_transaction:
            self.conn.commit()

    def initialize_schema(self) -> None:
        existing_version = self._existing_schema_version()
        if existing_version is None and self._has_csegraph_objects():
            raise IndexRequiredError()
        if existing_version is not None and existing_version != SCHEMA_VERSION:
            raise IndexRequiredError()

        # Keep first-use schema publication atomic. Without an explicit
        # transaction, executescript can expose tables before schema_version,
        # causing a concurrent opener to misclassify a valid build as outdated.
        version_upsert = METADATA_UPSERT.replace("?", f"'{SCHEMA_VERSION}'", 1)
        try:
            self.conn.executescript(
                "BEGIN EXCLUSIVE;\n"
                f"{SCHEMA_DDL}\n"
                f"{version_upsert};\n"
                f"PRAGMA user_version = {SCHEMA_USER_VERSION};\n"
                "COMMIT;\n"
            )
        except Exception:
            self.conn.rollback()
            raise

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
        include_roots: Optional[Sequence[str]] = None,
        indexed_untracked_paths: Optional[Sequence[str]] = None,
    ) -> None:
        now = time.time()
        existing = self.metadata(raise_if_empty=False)
        created_at = existing.get("created_at", str(now))
        branch, commit = git_head_state(root_dir)
        rows = {
            "schema_version": SCHEMA_VERSION,
            "root_dir": root_dir,
            "created_at": created_at,
            "updated_at": str(now),
            "built_branch": branch or "",
            "built_commit": commit or "",
            "index_revision": existing.get("index_revision", "0"),
        }
        if include_roots is not None:
            rows["include_roots"] = json.dumps(list(include_roots), sort_keys=True)
        if indexed_untracked_paths is not None:
            rows["indexed_untracked_paths"] = json.dumps(
                sorted(set(indexed_untracked_paths)),
                separators=(",", ":"),
            )
        self.conn.executemany(
            """
            INSERT INTO metadata(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            sorted(rows.items()),
        )
        self._commit()

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
        self._commit()
        return revision

    def checkpoint_git_state(self, root_dir: str) -> None:
        """Record the Git state only after the indexed graph is current."""
        branch, commit = git_head_state(root_dir)
        self.conn.executemany(
            """
            INSERT INTO metadata(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (
                ("built_branch", branch or ""),
                ("built_commit", commit or ""),
                ("updated_at", str(time.time())),
            ),
        )
        self._commit()

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
        self.conn.execute("DELETE FROM lexical_index")
        self.conn.execute("DELETE FROM summaries")
        self.conn.execute("DELETE FROM edges")
        self.conn.execute("DELETE FROM files")
        self._commit()

    def delete_file_payload(self, rel_path: str, remove_incoming: bool) -> List[str]:
        file_id = file_node_id(rel_path)
        file_row = self.conn.execute(
            "SELECT id FROM files WHERE id = ?",
            (file_id,),
        ).fetchone()
        if file_row is None:
            return []

        symbol_ids = [
            row["id"]
            for row in self.conn.execute(
                "SELECT id FROM symbols WHERE file_id = ?",
                (file_id,),
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
        self.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        self.conn.execute(
            f"DELETE FROM lexical_index WHERE node_id IN ({placeholders})",
            node_ids,
        )
        self.conn.execute(
            f"DELETE FROM summaries WHERE node_id IN ({placeholders})",
            node_ids,
        )
        self._commit()
        return symbol_ids

    def cleanup_orphan_edges(self) -> None:
        self.conn.execute(
            """
            DELETE FROM edges
             WHERE source NOT IN (SELECT id FROM entities)
                OR target NOT IN (SELECT id FROM entities)
            """
        )
        self._commit()

    def verify_lease(self, repo_root: str, owner: str) -> bool:
        row = self.conn.execute(
            "SELECT owner, expires_at FROM refresh_leases WHERE repo_root = ?",
            (repo_root,),
        ).fetchone()
        return row is not None and row["owner"] == owner and float(row["expires_at"]) > time.time()

    def validate_integrity(self) -> None:
        foreign_key_errors = self.conn.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError(f"Foreign-key validation failed: {foreign_key_errors!r}")
        integrity = self.conn.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            result = integrity[0] if integrity else "no result"
            raise RuntimeError(f"SQLite integrity validation failed: {result}")

    def optimize(self) -> None:
        self.conn.execute("PRAGMA optimize")
