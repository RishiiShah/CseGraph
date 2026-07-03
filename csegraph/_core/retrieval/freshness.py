from __future__ import annotations

import concurrent.futures
import sqlite3
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from csegraph._core.index.migrations import migrate_schema
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.index.schema import SCHEMA_VERSION
from csegraph._core.index.services import IndexService, RefreshService
from csegraph._core.languages.base import sha256_text
from csegraph._core.languages.registry import registry

AUTO_INDEX_FILE_LIMIT = 500
AUTO_REFRESH_TIMEOUT_SECONDS = 5.0
REFRESH_LEASE_SECONDS = 30.0
_REFRESH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="csegraph-refresh",
)


@dataclass
class FreshnessResult:
    state: str
    revision: int = 0
    refreshed_files: int = 0
    status: str | None = None
    warnings: list[str] = field(default_factory=list)
    next: dict[str, Any] | None = None


class FreshnessCoordinator:
    def __init__(
        self,
        db_path: str | Path,
        *,
        auto_index_file_limit: int = AUTO_INDEX_FILE_LIMIT,
        refresh_timeout: float = AUTO_REFRESH_TIMEOUT_SECONDS,
        lease_seconds: float = REFRESH_LEASE_SECONDS,
        lease_renew_interval: float | None = None,
    ) -> None:
        self.db_path = str(Path(db_path))
        self.auto_index_file_limit = auto_index_file_limit
        self.refresh_timeout = refresh_timeout
        self.lease_seconds = max(0.1, lease_seconds)
        default_renew_interval = min(5.0, self.lease_seconds / 3.0)
        requested_renew_interval = (
            lease_renew_interval
            if lease_renew_interval is not None
            else default_renew_interval
        )
        self.lease_renew_interval = min(
            max(0.02, requested_renew_interval),
            self.lease_seconds / 2.0,
        )

    def ensure_current(self, repo: str | Path | None) -> FreshnessResult:
        repo_path = Path(repo).resolve() if repo is not None else None
        if not self._has_index():
            if repo_path is None:
                return self._index_required("Repository path is required to create an index.")
            return self._bootstrap(repo_path)

        index = ProjectIndex(self.db_path)
        try:
            self._migrate(index)
            index.initialize_schema()
            metadata = index.metadata()
            indexed_repo = Path(metadata["root_dir"]).resolve()
            if repo_path is not None and repo_path != indexed_repo:
                return self._index_required(
                    "The supplied repository does not match the repository stored in this index."
                )
            repo_path = indexed_repo
            revision = index.index_revision()
            changed_paths = _detect_changed_paths(index, repo_path, metadata)
            if not changed_paths:
                current_commit = _run_git(
                    repo_path, ["rev-parse", "--short=12", "HEAD"]
                )
                current_branch = _run_git(
                    repo_path, ["rev-parse", "--abbrev-ref", "HEAD"]
                )
                if (
                    metadata.get("built_commit", "") != (current_commit or "")
                    or metadata.get("built_branch", "") != (current_branch or "")
                ):
                    index.checkpoint_git_state(str(repo_path))
                return FreshnessResult(state="current", revision=revision)
            profile = metadata.get("active_profile") or "auto"
        finally:
            index.close()

        owner = uuid.uuid4().hex
        if not self._acquire_lease(str(repo_path), owner):
            return self._wait_for_active_refresh(str(repo_path), revision)

        future = _REFRESH_EXECUTOR.submit(
            self._refresh_and_release,
            str(repo_path),
            owner,
            profile,
            changed_paths,
        )
        try:
            result = future.result(timeout=self.refresh_timeout)
        except concurrent.futures.TimeoutError:
            return FreshnessResult(
                state="refreshing",
                revision=revision,
                status="refresh_required",
                warnings=[
                    f"Automatic refresh exceeded {self.refresh_timeout:g} seconds; no stale context was returned."
                ],
                next={
                    "tool": "csegraph_refresh",
                    "reason": "Wait for or explicitly complete the in-progress refresh.",
                },
            )
        except Exception as exc:
            return FreshnessResult(
                state="refresh_failed",
                revision=revision,
                status="refresh_required",
                warnings=[f"Automatic refresh failed: {exc}"],
                next={
                    "tool": "csegraph_refresh",
                    "reason": "Refresh the changed files before retrieving context.",
                },
            )
        return result

    def _has_index(self) -> bool:
        db = Path(self.db_path)
        if not db.exists() or db.stat().st_size == 0:
            return False
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                row = conn.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name IN ('metadata', 'schema_meta')
                    LIMIT 1
                    """
                ).fetchone()
                if row is None:
                    return False
                metadata_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
                ).fetchone()
                if metadata_exists is None:
                    return True
                root = conn.execute(
                    "SELECT 1 FROM metadata WHERE key='root_dir'"
                ).fetchone()
                version = conn.execute(
                    "SELECT 1 FROM metadata WHERE key='schema_version'"
                ).fetchone()
                return root is not None or version is not None
            finally:
                conn.close()
        except sqlite3.Error:
            return False

    def _bootstrap(self, repo: Path) -> FreshnessResult:
        file_count = 0
        for _parser, _path in registry.iter_files(repo):
            file_count += 1
            if file_count > self.auto_index_file_limit:
                return self._index_required(
                    f"Repository has more than {self.auto_index_file_limit} discoverable source files."
                )
        try:
            IndexService(self.db_path).index(repo, profile="auto")
        except Exception as exc:
            return FreshnessResult(
                state="missing",
                status="index_required",
                warnings=[f"Automatic index creation failed: {exc}"],
                next={"tool": "csegraph_index", "reason": "Build the repository index."},
            )
        index = ProjectIndex(self.db_path)
        try:
            return FreshnessResult(
                state="indexed",
                revision=index.index_revision(),
                refreshed_files=file_count,
            )
        finally:
            index.close()

    def _index_required(self, reason: str) -> FreshnessResult:
        return FreshnessResult(
            state="missing",
            status="index_required",
            warnings=[reason],
            next={"tool": "csegraph_index", "reason": "Build the repository index."},
        )

    def _migrate(self, index: ProjectIndex) -> None:
        table = None
        for candidate in ("metadata", "schema_meta"):
            exists = index.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (candidate,),
            ).fetchone()
            if exists is not None:
                table = candidate
                break
        if table is None:
            return
        row = index.conn.execute(
            f"SELECT value FROM {table} WHERE key='schema_version'"
        ).fetchone()
        if row is not None and row["value"] != SCHEMA_VERSION:
            migrate_schema(index.conn, str(row["value"]))
            index.conn.commit()

    def _acquire_lease(self, repo_root: str, owner: str) -> bool:
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

    def _renew_lease(self, repo_root: str, owner: str) -> bool:
        index = ProjectIndex(self.db_path)
        try:
            now = time.time()
            index.conn.execute("BEGIN IMMEDIATE")
            cursor = index.conn.execute(
                """
                UPDATE refresh_leases
                SET expires_at = ?
                WHERE repo_root = ? AND owner = ? AND expires_at > ?
                """,
                (now + self.lease_seconds, repo_root, owner, now),
            )
            index.conn.commit()
            return cursor.rowcount == 1
        finally:
            index.close()

    def _release_lease(self, repo_root: str, owner: str) -> None:
        index = ProjectIndex(self.db_path)
        try:
            index.conn.execute(
                "DELETE FROM refresh_leases WHERE repo_root = ? AND owner = ?",
                (repo_root, owner),
            )
            index.conn.commit()
        finally:
            index.close()

    def _refresh_and_release(
        self,
        repo_root: str,
        owner: str,
        profile: str,
        changed_paths: list[Path],
    ) -> FreshnessResult:
        stop_renewal = threading.Event()
        lease_lost = threading.Event()
        confirmed_until = [time.time() + self.lease_seconds]

        def renew_lease() -> None:
            while not stop_renewal.wait(self.lease_renew_interval):
                try:
                    if not self._renew_lease(repo_root, owner):
                        lease_lost.set()
                        return
                    confirmed_until[0] = time.time() + self.lease_seconds
                except sqlite3.Error:
                    # A short-lived writer may delay renewal. Once the last
                    # confirmed lease window has elapsed, however, this owner
                    # must be fenced out even if SQLite remained busy.
                    if time.time() >= confirmed_until[0]:
                        lease_lost.set()
                        return

        renewal_thread = threading.Thread(
            target=renew_lease,
            name=f"csegraph-refresh-lease-{owner[:8]}",
            daemon=True,
        )
        renewal_thread.start()
        try:
            if not self._renew_lease(repo_root, owner):
                raise RuntimeError("Refresh lease ownership was lost before refresh.")
            confirmed_until[0] = time.time() + self.lease_seconds
            result = RefreshService(self.db_path).refresh(
                profile=profile,
                changed_paths=changed_paths,
            )
            # This synchronous renewal is also the final ownership fence. It
            # prevents an expired or replaced owner from reporting successful
            # freshness even if its refresh call happened to finish.
            if lease_lost.is_set() or not self._renew_lease(repo_root, owner):
                raise RuntimeError("Refresh lease ownership was lost before completion.")
            index = ProjectIndex(self.db_path)
            try:
                revision = index.index_revision()
            finally:
                index.close()
            return FreshnessResult(
                state="refreshed",
                revision=revision,
                refreshed_files=len(set(result.changed_files) | set(result.deleted_files)),
                warnings=list(result.warnings),
            )
        finally:
            stop_renewal.set()
            renewal_thread.join(timeout=max(1.0, self.lease_renew_interval * 2.0))
            self._release_lease(repo_root, owner)

    def _wait_for_active_refresh(
        self,
        repo_root: str,
        previous_revision: int,
    ) -> FreshnessResult:
        deadline = time.monotonic() + self.refresh_timeout
        while time.monotonic() < deadline:
            index = ProjectIndex(self.db_path)
            try:
                lease = index.conn.execute(
                    "SELECT expires_at FROM refresh_leases WHERE repo_root = ?",
                    (repo_root,),
                ).fetchone()
                revision = index.index_revision()
            finally:
                index.close()
            if lease is None or float(lease["expires_at"]) <= time.time():
                if revision > previous_revision:
                    return FreshnessResult(state="current", revision=revision)
                break
            time.sleep(0.05)
        return FreshnessResult(
            state="refreshing",
            revision=previous_revision,
            status="refresh_required",
            warnings=["Another process is refreshing the index; no stale context was returned."],
            next={
                "tool": "csegraph_context",
                "reason": "Retry after the active refresh completes.",
            },
        )


def _detect_changed_paths(
    index: ProjectIndex,
    repo: Path,
    metadata: dict[str, str],
) -> list[Path]:
    git_paths = _git_changed_paths(repo, metadata.get("built_commit"))
    if git_paths is not None:
        return _filter_indexed_content(index, repo, git_paths)
    return _filesystem_changed_paths(index, repo)


def _git_changed_paths(repo: Path, built_commit: str | None) -> list[Path] | None:
    head = _run_git(repo, ["rev-parse", "HEAD"])
    if head is None:
        return None

    commands: list[list[str]] = []
    if built_commit and not head.startswith(built_commit):
        commands.append(["diff", "--name-only", "-z", built_commit, "HEAD", "--"])
    commands.extend(
        [
            ["diff", "--name-only", "-z", "HEAD", "--"],
            ["ls-files", "--others", "--exclude-standard", "-z"],
        ]
    )
    changed: set[Path] = set()
    for args in commands:
        output = _run_git_bytes(repo, args)
        if output is None:
            return None
        for raw in output.split(b"\0"):
            if raw:
                changed.add(repo / raw.decode("utf-8", errors="replace"))
    return sorted(changed)


def _run_git(repo: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _run_git_bytes(repo: Path, args: list[str]) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            timeout=2,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _filesystem_changed_paths(index: ProjectIndex, repo: Path) -> list[Path]:
    stored = {
        str(row["path"]): (
            int(row["size"]),
            float(row["mtime"]),
            str(row["sha256"]),
        )
        for row in index.conn.execute("SELECT path, size, mtime, sha256 FROM files")
    }
    current: dict[str, tuple[int, float]] = {}
    for _parser, path in registry.iter_files(repo):
        try:
            stat = path.stat()
            rel = path.resolve().relative_to(repo).as_posix()
        except (OSError, ValueError):
            continue
        current[rel] = (int(stat.st_size), float(stat.st_mtime))

    changed: set[Path] = set()
    for rel, fingerprint in current.items():
        previous = stored.get(rel)
        if previous is None:
            changed.add(repo / rel)
            continue
        if previous[:2] == fingerprint:
            continue
        path = repo / rel
        try:
            current_hash = sha256_text(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            changed.add(path)
            continue
        if current_hash != previous[2]:
            changed.add(path)
    changed.update(repo / rel for rel in stored.keys() - current.keys())
    return sorted(changed)


def _filter_indexed_content(
    index: ProjectIndex,
    repo: Path,
    paths: list[Path],
) -> list[Path]:
    stored = {
        str(row["path"]): str(row["sha256"])
        for row in index.conn.execute("SELECT path, sha256 FROM files")
    }
    changed: set[Path] = {
        repo / rel_path
        for rel_path in stored
        if not (repo / rel_path).exists()
    }
    for path in paths:
        try:
            rel = path.resolve().relative_to(repo).as_posix()
        except ValueError:
            continue
        expected = stored.get(rel)
        if expected is None or not path.exists():
            changed.add(path)
            continue
        try:
            current = sha256_text(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            changed.add(path)
            continue
        if current != expected:
            changed.add(path)
    return sorted(changed)
