"""Shared repo-local path policy helpers."""

from __future__ import annotations

from pathlib import Path


def repo_scratch_root(repo_root: str | Path) -> Path:
    """Return the canonical repo-local scratch root for csegraph artifacts."""
    return Path(repo_root).resolve() / ".scratch" / "csegraph"


def assert_repo_local_path(path: str | Path, repo_root: str | Path, name: str) -> Path:
    """Resolve a path against the repo root and ensure it stays within that repo."""
    resolved_repo = Path(repo_root).resolve()
    candidate = Path(path)
    resolved_path = (
        candidate.resolve() if candidate.is_absolute() else (resolved_repo / candidate).resolve()
    )

    if resolved_path.is_relative_to(resolved_repo):
        return resolved_path

    scratch_root = repo_scratch_root(resolved_repo)
    raise ValueError(
        f"{name} path '{path}' must be within repository root '{resolved_repo}' "
        f"or repo-local scratch root '{scratch_root}'."
    )


def assert_safe_db_path(path: str | Path, repo_root: str | Path, name: str = "Database") -> Path:
    """Typed wrapper for repo-local DB path validation."""
    return assert_repo_local_path(path, repo_root, name)


def ensure_scratch_root(repo_root: str | Path) -> Path:
    """Create the repo-local scratch root if it does not already exist."""
    scratch_root = repo_scratch_root(repo_root)
    scratch_root.mkdir(parents=True, exist_ok=True)
    return scratch_root
