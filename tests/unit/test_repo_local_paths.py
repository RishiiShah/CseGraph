"""Repo-local path policy (assert_repo_local_path / assert_safe_db_path).

CseGraph keeps indexes, exports, and MCP ``db`` paths inside the repository tree
so artifacts stay with the project and agents do not write to shared OS temp dirs.

Positive tests elsewhere use ``<repo>/.scratch/csegraph/`` or ``<repo>/.csegraph/``.
Integration tests that call ``tempfile.gettempdir()`` are *negative* tests: they
assert that paths outside the repo raise ``ValueError``.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from csegraph._core.core.paths import assert_repo_local_path, assert_safe_db_path


def test_scratch_path_under_repo_is_allowed(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    scratch_db = repo / ".scratch" / "csegraph" / "index.db"
    scratch_db.parent.mkdir(parents=True)

    resolved = assert_safe_db_path(scratch_db, repo)

    assert resolved == scratch_db.resolve()


def test_system_temp_path_is_rejected(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    outside = Path(tempfile.gettempdir()) / "outside-csegraph.db"

    with pytest.raises(ValueError, match="must be within repository root"):
        assert_repo_local_path(outside, repo, "Database")
