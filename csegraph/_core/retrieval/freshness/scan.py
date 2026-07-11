"""Filesystem and Git change detection for freshness coordination."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from csegraph._core.ignore import (
    GITIGNORE_FILENAME,
    IGNORE_FILENAME,
    IgnoreFilter,
    _rules_from_file,
    load_ignore_filter,
)
from csegraph._core.index.ingestion import (
    _include_roots_from_metadata,
    _is_included_rel_path,
)
from csegraph._core.index.refresh_plan import _stored_file_hashes
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.languages.base import EXCLUDED_DIRS, sha256_text
from csegraph._core.languages.registry import UnsupportedLanguageError, registry
from csegraph._core.repo_state import git_tracked_paths

TINY_FRESHNESS_FILE_LIMIT = 256
TINY_FRESHNESS_INDEXED_BYTES_LIMIT = 16 * 1024 * 1024
TINY_FRESHNESS_SCAN_FILE_LIMIT = TINY_FRESHNESS_FILE_LIMIT * 2


class _TinyScanFallback(Exception):
    pass


@dataclass(frozen=True)
class _FilesystemSnapshot:
    revision: int
    file_signatures: dict[str, tuple[int, float]]
    directory_mtimes: dict[str, float]
    control_mtimes: dict[str, float | None]


_NON_GIT_SNAPSHOTS: dict[str, _FilesystemSnapshot] = {}
_NON_GIT_SNAPSHOTS_LOCK = threading.Lock()


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
        return _filter_indexed_content(index, repo, git_paths, metadata)
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


def _dir_may_contain_include(rel_dir: str, include_roots: tuple[str, ...]) -> bool:
    if not include_roots:
        return True
    normalized = rel_dir.strip("/")
    return any(
        normalized == root or normalized.startswith(f"{root}/") or root.startswith(f"{normalized}/")
        for root in include_roots
    )


def _git_changed_paths(repo: Path, built_commit: str | None) -> list[Path] | None:
    head_state = _git_head_state_from_files(repo)
    head = head_state[1] if head_state is not None else _run_git(repo, ["rev-parse", "HEAD"])
    if not head:
        return None

    status_args = [
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--no-renames",
    ]
    status = _run_git_bytes(repo, status_args)
    if status is None:
        return None
    status_paths = _porcelain_status_paths(repo, status)
    if status_paths is None:
        return None

    changed: set[Path] = set(status_paths)
    if built_commit and not (head.startswith(built_commit) or built_commit.startswith(head)):
        output = _run_git_bytes(
            repo,
            ["diff", "--name-only", "--no-renames", "-z", built_commit, "HEAD", "--"],
        )
        if output is None:
            return None
        for raw in output.split(b"\0"):
            if raw:
                changed.add(repo / raw.decode("utf-8", errors="replace"))
    return sorted(changed)


def _porcelain_status_paths(repo: Path, output: bytes) -> list[Path] | None:
    changed: set[Path] = set()
    for record in output.split(b"\0"):
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            return None
        raw = record[3:]
        if not raw:
            return None
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
    cached = _cached_filesystem_changed_paths(index, repo)
    if cached is not None:
        return cached

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
    result = sorted(changed)
    if not result:
        _store_filesystem_snapshot(index, repo, current)
    return result


def _cached_filesystem_changed_paths(
    index: ProjectIndex,
    repo: Path,
) -> list[Path] | None:
    key = f"{index.db_path}\0{repo}"
    with _NON_GIT_SNAPSHOTS_LOCK:
        snapshot = _NON_GIT_SNAPSHOTS.get(key)
    if snapshot is None or snapshot.revision != index.index_revision():
        return None

    for rel, previous_directory_mtime in snapshot.directory_mtimes.items():
        path = repo if rel == "." else repo / rel
        try:
            directory_mtime = float(path.stat().st_mtime)
        except OSError:
            return None
        if directory_mtime != previous_directory_mtime:
            return None

    for rel, previous_control_mtime in snapshot.control_mtimes.items():
        path = repo / rel
        try:
            control_mtime: float | None = float(path.stat().st_mtime)
        except OSError:
            control_mtime = None
        if control_mtime != previous_control_mtime:
            return None

    for rel, previous_file_signature in snapshot.file_signatures.items():
        path = repo / rel
        try:
            file_stat = path.stat()
        except OSError:
            return [path]
        if not path.is_file() or path.is_symlink():
            return [path]
        file_signature = (int(file_stat.st_size), float(file_stat.st_mtime))
        if file_signature != previous_file_signature:
            return [path]
    return []


def _store_filesystem_snapshot(
    index: ProjectIndex,
    repo: Path,
    current: dict[str, tuple[int, float]],
) -> None:
    directory_mtimes = _directory_mtimes(repo)
    if directory_mtimes is None:
        return
    control_mtimes: dict[str, float | None] = {}
    for rel in (GITIGNORE_FILENAME, IGNORE_FILENAME):
        path = repo / rel
        try:
            control_mtimes[rel] = float(path.stat().st_mtime)
        except OSError:
            control_mtimes[rel] = None
    snapshot = _FilesystemSnapshot(
        revision=index.index_revision(),
        file_signatures=current,
        directory_mtimes=directory_mtimes,
        control_mtimes=control_mtimes,
    )
    key = f"{index.db_path}\0{repo}"
    with _NON_GIT_SNAPSHOTS_LOCK:
        _NON_GIT_SNAPSHOTS[key] = snapshot


def _directory_mtimes(repo: Path) -> dict[str, float] | None:
    resolved = repo.resolve()
    mtimes: dict[str, float] = {}
    try:
        for dirpath, dirnames, _filenames in os.walk(resolved):
            dirnames[:] = sorted(
                name for name in dirnames if name not in EXCLUDED_DIRS and not name.startswith(".")
            )
            path = Path(dirpath)
            rel = path.relative_to(resolved).as_posix()
            mtimes[rel if rel != "." else "."] = float(path.stat().st_mtime)
    except (OSError, ValueError):
        return None
    return mtimes


def _filter_indexed_content(
    index: ProjectIndex,
    repo: Path,
    paths: list[Path],
    metadata: dict[str, str],
) -> list[Path]:
    raw_untracked = metadata.get("indexed_untracked_paths")
    indexed_untracked: list[str]
    if raw_untracked is not None:
        try:
            decoded = json.loads(raw_untracked)
        except (TypeError, json.JSONDecodeError):
            decoded = []
        indexed_untracked = (
            [str(path) for path in decoded if isinstance(path, str)]
            if isinstance(decoded, list)
            else []
        )
        if not paths and all((repo / rel_path).exists() for rel_path in indexed_untracked):
            return []

    ignore = load_ignore_filter(repo)
    include_roots = _include_roots_from_metadata(metadata)
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
