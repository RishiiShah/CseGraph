from __future__ import annotations

import concurrent.futures
import json
import os
import sqlite3
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from csegraph._core.core.errors import IndexRequiredError
from csegraph._core.core.models import RefreshResult
from csegraph._core.ignore import (
    GITIGNORE_FILENAME,
    IGNORE_FILENAME,
    IgnoreFilter,
    _rules_from_file,
    load_ignore_filter,
)
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.index.services import IndexService, RefreshService
from csegraph._core.languages.base import EXCLUDED_DIRS, sha256_text
from csegraph._core.languages.registry import UnsupportedLanguageError, registry
from csegraph._core.repo_state import git_tracked_paths

AUTO_INDEX_FILE_LIMIT = 500
AUTO_REFRESH_TIMEOUT_SECONDS = 5.0
REFRESH_LEASE_SECONDS = 30.0
TINY_FRESHNESS_FILE_LIMIT = 256
TINY_FRESHNESS_INDEXED_BYTES_LIMIT = 16 * 1024 * 1024
TINY_FRESHNESS_SCAN_FILE_LIMIT = TINY_FRESHNESS_FILE_LIMIT * 2
_REFRESH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="csegraph-refresh",
)
_T = TypeVar("_T")


class _TinyScanFallback(Exception):
    pass


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
                WHERE repo_root = ? AND owner = ?
                """,
                (now + self.lease_seconds, repo_root, owner),
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


def _detect_changed_paths(
    index: ProjectIndex,
    repo: Path,
    metadata: dict[str, str],
) -> list[Path]:
    if _is_tiny_index(index):
        filesystem_paths = _tiny_filesystem_changed_paths(index, repo, metadata)
        if filesystem_paths is not None:
            return filesystem_paths
    git_paths = _git_changed_paths(repo, metadata.get("built_commit"))
    if git_paths is not None:
        return _filter_indexed_content(index, repo, git_paths)
    return _filesystem_changed_paths(index, repo)


def _is_tiny_index(index: ProjectIndex) -> bool:
    row = index.conn.execute(
        """
        SELECT COUNT(*) AS file_count, COALESCE(SUM(size), 0) AS total_bytes
        FROM files
        """
    ).fetchone()
    if row is None:
        return False
    return (
        int(row["file_count"]) <= TINY_FRESHNESS_FILE_LIMIT
        and int(row["total_bytes"]) <= TINY_FRESHNESS_INDEXED_BYTES_LIMIT
    )


def _tiny_filesystem_changed_paths(
    index: ProjectIndex,
    repo: Path,
    metadata: dict[str, str],
) -> list[Path] | None:
    stored = {
        str(row["path"]): (int(row["size"]), str(row["sha256"]))
        for row in index.conn.execute("SELECT path, size, sha256 FROM files")
    }
    include_roots = _include_roots_from_metadata(metadata)
    changed: set[Path] = set()

    for rel, previous in stored.items():
        path = repo / rel
        try:
            stat = path.stat()
        except OSError:
            changed.add(path)
            continue
        if not path.is_file() or path.is_symlink():
            changed.add(path)
            continue
        if int(stat.st_size) != previous[0]:
            changed.add(path)
            continue
        try:
            current_hash = sha256_text(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            changed.add(path)
            continue
        if current_hash != previous[1]:
            changed.add(path)

    scan_limit = max(TINY_FRESHNESS_SCAN_FILE_LIMIT, len(stored) + TINY_FRESHNESS_FILE_LIMIT)
    discovered = 0
    try:
        for rel in _iter_tiny_filesystem_source_paths(repo, include_roots):
            discovered += 1
            if discovered > scan_limit:
                return None
            if rel not in stored:
                changed.add(repo / rel)
    except _TinyScanFallback:
        return None
    return sorted(changed)


def _iter_tiny_filesystem_source_paths(
    repo: Path,
    include_roots: tuple[str, ...],
) -> Iterator[str]:
    if (repo / ".csegraphinclude").exists():
        raise _TinyScanFallback()
    ignore = _tiny_ignore_filter(repo)
    resolved_repo = repo.resolve()
    for dirpath, dirnames, filenames in os.walk(resolved_repo):
        rel_root = Path(dirpath).resolve().relative_to(resolved_repo).as_posix()
        if rel_root != "." and ".gitignore" in filenames:
            raise _TinyScanFallback()
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in EXCLUDED_DIRS
            and not name.startswith(".")
            and _dir_may_contain_include(
                f"{rel_root}/{name}" if rel_root != "." else name,
                include_roots,
            )
            and ignore.should_descend(f"{rel_root}/{name}" if rel_root != "." else name)
        )
        for filename in sorted(filenames):
            rel = f"{rel_root}/{filename}" if rel_root != "." else filename
            if include_roots and not _is_included_rel_path(rel, include_roots):
                continue
            try:
                parser = registry.for_extension(Path(filename).suffix)
            except UnsupportedLanguageError:
                continue
            if ignore.is_ignored(rel):
                raise _TinyScanFallback()
            if parser.excludes_rel_path(rel):
                continue
            path = resolved_repo / rel
            if path.is_file() and not path.is_symlink():
                yield rel


def _tiny_ignore_filter(repo: Path) -> IgnoreFilter:
    rules = [
        *_rules_from_file(repo / GITIGNORE_FILENAME, "gitignore"),
        *_rules_from_file(repo / IGNORE_FILENAME, "csegraphignore"),
    ]
    return IgnoreFilter(rules, root=repo.resolve())


def _include_roots_from_metadata(metadata: dict[str, str]) -> tuple[str, ...]:
    try:
        decoded = json.loads(metadata.get("include_roots", "[]"))
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(decoded, list):
        return ()
    return tuple(str(value).strip("/") for value in decoded if isinstance(value, str))


def _is_included_rel_path(rel_path: str, include_roots: tuple[str, ...]) -> bool:
    if not include_roots:
        return True
    normalized = rel_path.strip("/")
    return any(normalized == root or normalized.startswith(f"{root}/") for root in include_roots)


def _dir_may_contain_include(rel_dir: str, include_roots: tuple[str, ...]) -> bool:
    if not include_roots:
        return True
    normalized = rel_dir.strip("/")
    return any(
        normalized == root
        or normalized.startswith(f"{root}/")
        or root.startswith(f"{normalized}/")
        for root in include_roots
    )


def _git_changed_paths(repo: Path, built_commit: str | None) -> list[Path] | None:
    head = _run_git(repo, ["rev-parse", "HEAD"])
    if head is None:
        return None

    commands: list[list[str]] = []
    if built_commit and not head.startswith(built_commit):
        commands.append(["diff", "--name-only", "--no-renames", "-z", built_commit, "HEAD", "--"])
    commands.extend(
        [
            ["diff", "--name-only", "--no-renames", "-z", "HEAD", "--"],
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


def _git_head_state(repo: Path) -> tuple[str | None, str | None]:
    state = _git_head_state_from_files(repo)
    if state is not None:
        return state
    current_commit = _run_git(repo, ["rev-parse", "--short=12", "HEAD"])
    current_branch = _run_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    return current_branch, current_commit


def _git_head_state_from_files(repo: Path) -> tuple[str | None, str | None] | None:
    git_dir = _git_dir(repo)
    if git_dir is None:
        return None
    head_path = git_dir / "HEAD"
    try:
        head = head_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head:
        return None

    if not head.startswith("ref: "):
        return "HEAD", head[:12]

    ref = head[5:].strip()
    commit = _read_git_ref(git_dir, ref)
    if commit is None:
        return None
    branch = ref.removeprefix("refs/heads/")
    return branch, commit[:12]


def _git_dir(repo: Path) -> Path | None:
    dot_git = repo / ".git"
    if dot_git.is_dir():
        return dot_git
    if not dot_git.is_file():
        return None
    try:
        text = dot_git.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    prefix = "gitdir:"
    if not text.lower().startswith(prefix):
        return None
    raw_path = text[len(prefix) :].strip()
    path = Path(raw_path)
    if not path.is_absolute():
        path = dot_git.parent / path
    return path.resolve()


def _read_git_ref(git_dir: Path, ref: str) -> str | None:
    for base in _git_ref_bases(git_dir):
        ref_path = base / ref
        try:
            commit = ref_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if commit:
            return commit
    for base in _git_ref_bases(git_dir):
        packed = base / "packed-refs"
        try:
            lines = packed.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        suffix = f" {ref}"
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "^")):
                continue
            if stripped.endswith(suffix):
                return stripped.split(" ", 1)[0]
    return None


def _git_ref_bases(git_dir: Path) -> tuple[Path, ...]:
    common_dir_file = git_dir / "commondir"
    try:
        raw_common_dir = common_dir_file.read_text(encoding="utf-8").strip()
    except OSError:
        return (git_dir,)
    if not raw_common_dir:
        return (git_dir,)
    common_dir = Path(raw_common_dir)
    if not common_dir.is_absolute():
        common_dir = git_dir / common_dir
    common_dir = common_dir.resolve()
    return (git_dir, common_dir) if common_dir != git_dir else (git_dir,)


def _checkpoint_git_state(
    index: ProjectIndex,
    branch: str | None,
    commit: str | None,
) -> None:
    index.conn.executemany(
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
    index.conn.commit()


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
    metadata = index.metadata(raise_if_empty=False)
    ignore = load_ignore_filter(repo)
    try:
        include_roots = tuple(
            str(value).strip("/")
            for value in json.loads(metadata.get("include_roots", "[]"))
            if isinstance(value, str)
        )
    except (TypeError, json.JSONDecodeError):
        include_roots = ()
    raw_untracked = metadata.get("indexed_untracked_paths")
    if raw_untracked is None:
        stored = {
            str(row["path"]): str(row["sha256"])
            for row in index.conn.execute("SELECT path, sha256 FROM files")
        }
        tracked = git_tracked_paths(str(repo))
        if tracked is not None:
            indexed_untracked = sorted(set(stored) - tracked)
            index.conn.execute(
                """
                INSERT INTO metadata(key, value)
                VALUES('indexed_untracked_paths', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (json.dumps(indexed_untracked, separators=(",", ":")),),
            )
            index.conn.commit()
        else:
            indexed_untracked = list(stored)
    else:
        try:
            decoded = json.loads(raw_untracked)
        except (TypeError, json.JSONDecodeError):
            decoded = []
        indexed_untracked = (
            [str(path) for path in decoded if isinstance(path, str)]
            if isinstance(decoded, list)
            else []
        )
        candidate_rel_paths = set(indexed_untracked)
        for path in paths:
            try:
                candidate_rel_paths.add(path.resolve().relative_to(repo).as_posix())
            except ValueError:
                continue
        stored = _stored_file_hashes(index, candidate_rel_paths)

    changed: set[Path] = {
        repo / rel_path
        for rel_path in indexed_untracked
        if rel_path in stored and not (repo / rel_path).exists()
    }
    for path in paths:
        try:
            rel = path.resolve().relative_to(repo).as_posix()
        except ValueError:
            continue
        expected = stored.get(rel)
        if not path.exists():
            changed.add(path)
            continue
        if expected is None:
            if ignore.is_ignored(rel) or (
                include_roots
                and not any(rel == root or rel.startswith(f"{root}/") for root in include_roots)
            ):
                continue
            try:
                registry.for_extension(path.suffix)
            except UnsupportedLanguageError:
                continue
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


def _stored_file_hashes(
    index: ProjectIndex,
    rel_paths: set[str],
) -> dict[str, str]:
    stored: dict[str, str] = {}
    ordered = sorted(rel_paths)
    for offset in range(0, len(ordered), 400):
        batch = ordered[offset : offset + 400]
        placeholders = ",".join("?" for _ in batch)
        stored.update(
            {
                str(row["path"]): str(row["sha256"])
                for row in index.conn.execute(
                    f"SELECT path, sha256 FROM files WHERE path IN ({placeholders})",
                    tuple(batch),
                )
            }
        )
    return stored
