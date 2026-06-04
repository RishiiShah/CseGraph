"""Version-control helpers for file discovery (git-first, SVN fallback)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional, Set

_SVN_TIMEOUT = 60


def find_svn_root(start: Path) -> Optional[Path]:
    """Walk up from *start* to find the SVN working copy root (topmost ``.svn``)."""
    current = start.resolve()
    candidate: Optional[Path] = None
    while True:
        if (current / ".svn").is_dir():
            candidate = current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return candidate


def svn_versioned_paths(svn_root: Path, scan_root: Path) -> Set[str]:
    """Return repo-relative paths from ``svn list -R`` under *scan_root*.

    Only includes paths that exist as regular files in the working copy.
    Returns an empty set when ``svn`` is unavailable or listing fails.
    """
    svn_root = svn_root.resolve()
    scan_root = scan_root.resolve()
    try:
        scan_prefix = scan_root.relative_to(svn_root).as_posix()
    except ValueError:
        return set()

    try:
        result = subprocess.run(
            ["svn", "list", "--recursive", "--non-interactive"],
            cwd=svn_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SVN_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return set()
    if result.returncode != 0:
        return set()

    paths: Set[str] = set()
    for line in result.stdout.splitlines():
        git_rel = line.strip()
        if not git_rel or git_rel.endswith("/"):
            continue
        scan_rel = _to_scan_relative(git_rel, scan_prefix)
        if not scan_rel:
            continue
        if (scan_root / scan_rel).is_file():
            paths.add(scan_rel)
    return paths


def _to_scan_relative(vcs_rel: str, scan_prefix: str) -> Optional[str]:
    if not scan_prefix or scan_prefix == ".":
        return vcs_rel
    if vcs_rel == scan_prefix:
        return ""
    prefix = f"{scan_prefix}/"
    if vcs_rel.startswith(prefix):
        return vcs_rel[len(prefix) :]
    return None
