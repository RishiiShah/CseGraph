from __future__ import annotations

import subprocess
from typing import Optional, Tuple


def git_head_state(repo_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (branch, short_commit) for the repo, or (None, None) outside git."""
    branch = _run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    commit = _run_git(repo_path, "rev-parse", "--short=12", "HEAD")
    return branch, commit


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
