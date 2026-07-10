from __future__ import annotations

import subprocess
from typing import Iterable, Optional, Tuple


def git_head_state(repo_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (branch, short_commit) for the repo, or (None, None) outside git."""
    branch = _run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    commit = _run_git(repo_path, "rev-parse", "--short=12", "HEAD")
    return branch, commit


def git_tracked_paths(repo_path: str) -> Optional[set[str]]:
    return _run_git_paths(repo_path, "ls-files", "-z")


def git_untracked_paths(
    repo_path: str,
    candidates: Iterable[str] | None = None,
) -> Optional[set[str]]:
    args = ["ls-files", "--others", "--exclude-standard", "-z"]
    if candidates is not None:
        paths = sorted(set(candidates))
        if not paths:
            return set()
        args.extend(["--", *paths])
    return _run_git_paths(repo_path, *args)


def _run_git(repo_path: str, *args: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _run_git_paths(repo_path: str, *args: str) -> Optional[set[str]]:
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, *args],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return {
                raw.decode("utf-8", errors="replace") for raw in result.stdout.split(b"\0") if raw
            }
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return None
