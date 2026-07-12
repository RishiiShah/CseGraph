from __future__ import annotations

import concurrent.futures
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

from csegraph._core.core.errors import IndexRequiredError
from csegraph._core.core.models import RefreshResult
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.index.services import IndexService, RefreshService
from csegraph._core.languages.registry import registry
from csegraph._core.retrieval.freshness.lease import RefreshLease
from csegraph._core.retrieval.freshness.scan import (
    _checkpoint_git_state,
    _detect_changed_paths,
    _git_head_state,
)

AUTO_INDEX_FILE_LIMIT = 500
AUTO_REFRESH_TIMEOUT_SECONDS = 5.0
REFRESH_LEASE_SECONDS = 30.0
_REFRESH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="csegraph-refresh",
)
_T = TypeVar("_T")


@dataclass
class FreshnessResult:
    state: str
    revision: int = 0
    refreshed_files: int = 0
    status: str | None = None
    warnings: list[str] = field(default_factory=list)
    next: dict[str, Any] | None = None


def _reused_refresh_result(
    db_path: str,
    repo_path: Path,
) -> RefreshResult:
    del db_path, repo_path
    return RefreshResult(
        files_indexed=0,
        symbols_indexed=0,
        edges_indexed=0,
        cache_hits=0,
        cache_misses=0,
        unchanged_files=[],
        changed_files=[],
        deleted_files=[],
        parse_errors={},
        warnings=["Reused the revision published by another refresh process."],
        timings_ms={},
    )


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
        self._lease = RefreshLease(self.db_path, self.lease_seconds)
        default_renew_interval = min(5.0, self.lease_seconds / 3.0)
        requested_renew_interval = (
            lease_renew_interval if lease_renew_interval is not None else default_renew_interval
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
            try:
                index.initialize_schema()
            except IndexRequiredError:
                return self._index_required(
                    "The existing index uses an unsupported schema and must be rebuilt."
                )
            metadata = index.metadata(raise_if_empty=False)
            if "root_dir" not in metadata:
                if repo_path is None:
                    return self._index_required(
                        "Repository path is required while the index is initializing."
                    )
                return self._bootstrap(repo_path)
            indexed_repo = Path(metadata["root_dir"]).resolve()
            if repo_path is not None and repo_path != indexed_repo:
                return self._index_required(
                    "The supplied repository does not match the repository stored in this index."
                )
            repo_path = indexed_repo
            revision = index.index_revision()
            changed_paths = _detect_changed_paths(index, repo_path, metadata)
            if not changed_paths:
                current_branch, current_commit = _git_head_state(repo_path)
                if metadata.get("built_commit", "") != (current_commit or "") or metadata.get(
                    "built_branch", ""
                ) != (current_branch or ""):
                    if current_commit is None and current_branch is None:
                        index.checkpoint_git_state(str(repo_path))
                    else:
                        _checkpoint_git_state(index, current_branch, current_commit)
                return FreshnessResult(state="current", revision=revision)
        finally:
            index.close()

        owner = uuid.uuid4().hex
        if not self._acquire_lease(str(repo_path), owner):
            return self._wait_for_active_refresh(str(repo_path), revision)

        future = _REFRESH_EXECUTOR.submit(
            self._refresh_and_release,
            str(repo_path),
            owner,
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

    def explicit_refresh(self, repo: str | Path) -> Any:
        repo_path = Path(repo).resolve()

        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            indexed_repo = Path(metadata["root_dir"]).resolve()
            if indexed_repo != repo_path:
                raise ValueError("The supplied repository does not match the indexed repository.")
            revision = index.index_revision()
        finally:
            index.close()

        owner = uuid.uuid4().hex
        if not self._acquire_lease(str(repo_path), owner):
            result = self._wait_for_active_refresh(
                str(repo_path),
                revision,
                timeout=self.lease_seconds,
            )
            if result.state == "current":
                return _reused_refresh_result(self.db_path, repo_path)
            if not self._acquire_lease(str(repo_path), owner):
                raise RuntimeError("Another refresh is still in progress.")

        index = ProjectIndex(self.db_path)
        try:
            metadata = index.metadata()
            changed_paths = _detect_changed_paths(index, repo_path, metadata)
        finally:
            index.close()

        future = _REFRESH_EXECUTOR.submit(
            self._refresh_and_release,
            str(repo_path),
            owner,
            changed_paths,
            True,
        )
        return future.result()

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
                root = conn.execute("SELECT 1 FROM metadata WHERE key='root_dir'").fetchone()
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
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            revision = index.index_revision()
            metadata = index.metadata(raise_if_empty=False)
            if metadata.get("root_dir") == str(repo) and revision > 0:
                return FreshnessResult(
                    state="current",
                    revision=revision,
                    refreshed_files=0,
                )
        finally:
            index.close()

        owner = uuid.uuid4().hex
        if not self._acquire_lease(str(repo), owner):
            waited = self._wait_for_active_refresh(str(repo), revision)
            if waited.state == "current":
                return waited
            if not self._acquire_lease(str(repo), owner):
                return FreshnessResult(
                    state="initializing",
                    revision=revision,
                    status="refresh_required",
                    warnings=["Another process is building the initial index."],
                    next={
                        "tool": "csegraph_context",
                        "reason": "Retry after the active index build completes.",
                    },
                )

        try:
            result = self._index_and_release(str(repo), owner)
        except Exception as exc:
            return FreshnessResult(
                state="missing",
                status="index_required",
                warnings=[f"Automatic index creation failed: {exc}"],
                next={"tool": "csegraph_index", "reason": "Build the repository index."},
            )
        return FreshnessResult(
            state="indexed",
            revision=result,
            refreshed_files=file_count,
        )

    def _index_required(self, reason: str) -> FreshnessResult:
        return FreshnessResult(
            state="missing",
            status="index_required",
            warnings=[reason],
            next={"tool": "csegraph_index", "reason": "Build the repository index."},
        )

    def _acquire_lease(self, repo_root: str, owner: str) -> bool:
        return self._lease.acquire(repo_root, owner)

    def _renew_lease(self, repo_root: str, owner: str) -> bool:
        return self._lease.renew(repo_root, owner)

    def _release_lease(self, repo_root: str, owner: str) -> None:
        self._lease.release(repo_root, owner)

    def _refresh_and_release(
        self,
        repo_root: str,
        owner: str,
        changed_paths: list[Path] | None,
        return_full_result: bool = False,
    ) -> Any:
        def operation() -> Any:
            result = RefreshService(self.db_path).refresh(
                changed_paths=changed_paths,
                lease_owner=owner,
            )
            index = ProjectIndex(self.db_path)
            try:
                revision = index.index_revision()
            finally:
                index.close()

            if return_full_result:
                return result
            return FreshnessResult(
                state="refreshed",
                revision=revision,
                refreshed_files=len(set(result.changed_files) | set(result.deleted_files)),
                warnings=list(result.warnings),
            )

        return self._run_with_renewed_lease(
            repo_root,
            owner,
            "Refresh",
            operation,
        )

    def _index_and_release(self, repo_root: str, owner: str) -> int:
        def operation() -> int:
            IndexService(self.db_path).index(
                repo_root,
                lease_owner=owner,
            )
            index = ProjectIndex(self.db_path)
            try:
                return index.index_revision()
            finally:
                index.close()

        return self._run_with_renewed_lease(
            repo_root,
            owner,
            "Index",
            operation,
            replaces_database=True,
        )

    def _run_with_renewed_lease(
        self,
        repo_root: str,
        owner: str,
        operation_name: str,
        operation: Callable[[], _T],
        *,
        replaces_database: bool = False,
    ) -> _T:
        stop_renewal = threading.Event()

        def renew_lease() -> None:
            while not stop_renewal.wait(self.lease_renew_interval):
                try:
                    if not self._renew_lease(repo_root, owner):
                        return
                except sqlite3.Error:
                    continue

        renewal_thread = threading.Thread(
            target=renew_lease,
            name=f"csegraph-{operation_name.lower()}-lease-{owner[:8]}",
            daemon=True,
        )
        renewal_thread.start()
        try:
            if not self._renew_lease(repo_root, owner):
                raise RuntimeError(f"{operation_name} lease ownership was lost before starting.")
            result = operation()
            if not replaces_database and not self._renew_lease(repo_root, owner):
                raise RuntimeError(f"{operation_name} lease ownership was lost before completion.")
            return result
        finally:
            stop_renewal.set()
            renewal_thread.join(timeout=max(1.0, self.lease_renew_interval * 2.0))
            self._release_lease(repo_root, owner)

    def _wait_for_active_refresh(
        self,
        repo_root: str,
        previous_revision: int,
        timeout: float | None = None,
    ) -> FreshnessResult:
        timeout = timeout if timeout is not None else self.refresh_timeout
        deadline = time.monotonic() + timeout
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
