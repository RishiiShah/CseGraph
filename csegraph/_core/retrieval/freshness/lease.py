"""SQLite-backed ownership for serialized index refresh operations."""

from __future__ import annotations

import time
from pathlib import Path

from csegraph._core.index.repository import ProjectIndex


class RefreshLease:
    """Acquire, renew, and release one repository refresh lease."""

    def __init__(self, db_path: str | Path, lease_seconds: float) -> None:
        self.db_path = str(Path(db_path))
        self.lease_seconds = lease_seconds

    def acquire(self, repo_root: str, owner: str) -> bool:
        index = ProjectIndex(self.db_path)
        try:
            now = time.time()
            index.conn.execute("BEGIN IMMEDIATE")
            index.conn.execute("DELETE FROM refresh_leases WHERE expires_at <= ?", (now,))
            index.conn.execute(
                """
                INSERT OR IGNORE INTO refresh_leases(repo_root, owner, expires_at)
                VALUES(?, ?, ?)
                """,
                (repo_root, owner, now + self.lease_seconds),
            )
            row = index.conn.execute(
                "SELECT owner FROM refresh_leases WHERE repo_root = ?",
                (repo_root,),
            ).fetchone()
            index.conn.commit()
            return row is not None and row["owner"] == owner
        finally:
            index.close()

    def renew(self, repo_root: str, owner: str) -> bool:
        index = ProjectIndex(self.db_path)
        try:
            now = time.time()
            index.conn.execute("BEGIN IMMEDIATE")
            cursor = index.conn.execute(
                """
                UPDATE refresh_leases
                SET expires_at = ?
                WHERE repo_root = ? AND owner = ?
                """,
                (now + self.lease_seconds, repo_root, owner),
            )
            index.conn.commit()
            return cursor.rowcount == 1
        finally:
            index.close()

    def release(self, repo_root: str, owner: str) -> None:
        index = ProjectIndex(self.db_path)
        try:
            index.conn.execute(
                "DELETE FROM refresh_leases WHERE repo_root = ? AND owner = ?",
                (repo_root, owner),
            )
            index.conn.commit()
        finally:
            index.close()


__all__ = ["RefreshLease"]
