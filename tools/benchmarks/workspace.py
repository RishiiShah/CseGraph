"""Workspace preparation and hygiene for adaptive benchmark runs."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Sequence

from tools.benchmarks.models import BenchmarkRepository, PreparedRepository

BENCHMARK_ARTIFACT_NAMES = frozenset(
    {
        ".DS_Store",
        ".cache",
        ".coverage",
        ".csegraph",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
    }
)
BENCHMARK_ARTIFACT_SUFFIXES = (".pyc", ".pyo")
LOCAL_COPY_URLS = frozenset({"fixture://local", "sandbox://local"})


def copy_benchmark_repository(source: Path, destination: Path) -> dict[str, Any]:
    """Copy a benchmark repository into scratch without runtime artifacts."""

    shutil.copytree(source, destination, ignore=_benchmark_copy_ignore)
    return benchmark_workspace_hygiene(destination)


def benchmark_workspace_hygiene(path: Path) -> dict[str, Any]:
    """Report whether a benchmark workspace contains known runtime artifacts."""

    artifacts = [
        candidate.relative_to(path).as_posix()
        for candidate in path.rglob("*")
        if _is_benchmark_artifact_path(candidate.relative_to(path))
    ]
    return {
        "clean": not artifacts,
        "artifact_paths": sorted(artifacts)[:50],
        "artifact_count": len(artifacts),
        "ignored_names": sorted(BENCHMARK_ARTIFACT_NAMES),
        "ignored_suffixes": list(BENCHMARK_ARTIFACT_SUFFIXES),
    }


def _benchmark_copy_ignore(directory: str, names: Sequence[str]) -> set[str]:
    base = Path(directory)
    return {
        name
        for name in names
        if _is_benchmark_artifact_path((base / name).relative_to(base))
        or name in BENCHMARK_ARTIFACT_NAMES
        or name.endswith(BENCHMARK_ARTIFACT_SUFFIXES)
    }


def _is_benchmark_artifact_path(path: Path) -> bool:
    parts = path.parts
    return any(part in BENCHMARK_ARTIFACT_NAMES for part in parts) or path.name.endswith(
        BENCHMARK_ARTIFACT_SUFFIXES
    )


def prepare_benchmark_repository(
    repository: BenchmarkRepository,
    *,
    repo_root: Path,
    cache_root: Path,
    bootstrap_missing: bool,
) -> PreparedRepository:
    requested = (repo_root / repository.path).resolve()
    if requested.is_dir():
        observed = (
            _fixture_revision(requested)
            if repository.url == "fixture://local"
            else _git_commit(requested)
        )
        return PreparedRepository(
            path=requested,
            observed_commit=observed,
            commit_matches=observed == repository.commit,
            bootstrapped=False,
            reason=None if observed == repository.commit else "repository_commit_mismatch",
        )
    if not bootstrap_missing:
        return PreparedRepository(
            path=None,
            observed_commit=None,
            commit_matches=False,
            bootstrapped=False,
            reason="repository_missing; rerun with --bootstrap-missing",
        )

    destination = cache_root / _safe_repository_cache_name(repository)
    if not destination.is_dir():
        destination.parent.mkdir(parents=True, exist_ok=True)
        clone = subprocess.run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                repository.url,
                str(destination),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if clone.returncode != 0:
            return PreparedRepository(
                path=None,
                observed_commit=None,
                commit_matches=False,
                bootstrapped=False,
                reason=f"clone_failed: {clone.stderr.strip()[:300]}",
            )
    checkout = subprocess.run(
        ["git", "-C", str(destination), "checkout", "--detach", repository.commit],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if checkout.returncode != 0:
        fetch = subprocess.run(
            [
                "git",
                "-C",
                str(destination),
                "fetch",
                "--depth=1",
                "origin",
                repository.commit,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if fetch.returncode == 0:
            checkout = subprocess.run(
                ["git", "-C", str(destination), "checkout", "--detach", repository.commit],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
    observed = _git_commit(destination)
    return PreparedRepository(
        path=destination if checkout.returncode == 0 else None,
        observed_commit=observed,
        commit_matches=observed == repository.commit,
        bootstrapped=True,
        reason=(
            None
            if checkout.returncode == 0 and observed == repository.commit
            else f"checkout_failed: {checkout.stderr.strip()[:300]}"
        ),
    )


def _safe_repository_cache_name(repository: BenchmarkRepository) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", repository.path).strip("-")
    return f"{slug}-{repository.commit[:12]}"


def _git_commit(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _fixture_revision(repo: Path) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    for path in sorted(
        (
            candidate
            for candidate in repo.rglob("*")
            if candidate.is_file() and not _is_benchmark_artifact_path(candidate.relative_to(repo))
        ),
        key=lambda candidate: candidate.relative_to(repo).as_posix(),
    ):
        relative = path.relative_to(repo).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
