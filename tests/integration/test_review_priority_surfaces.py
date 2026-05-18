from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

from csegraph_core.core.models import PostprocessResult, StatusResult, to_dict
from csegraph_core.index.repository import ProjectIndex, json_dumps
from csegraph_core.index.services import IndexService
from csegraph_core.postprocess import PostprocessService
from csegraph_core.repo_state import _run_git, git_head_state
from csegraph_core.status import StatusService, _build_warnings, _epoch_to_iso


def _write_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.py").write_text(
        "from b import helper\n\n"
        "def main():\n"
        "    return helper()\n",
        encoding="utf-8",
    )
    (root / "b.py").write_text(
        "def helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )


def _init_git_repo(repo: Path) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, env=env)


def test_status_and_postprocess_results_serialize() -> None:
    status = StatusResult(
        command="status",
        db_path="index.db",
        repo_root="/repo",
        schema_version="csegraph-sqlite-v5",
        active_profile="small",
        total_nodes=1,
        total_edges=2,
        total_files=3,
        languages=["python"],
        parse_error_count=0,
    )
    postprocess = PostprocessResult(
        command="postprocess",
        db_path="index.db",
        repo_root="/repo",
        fts_entries=4,
        communities_detected=5,
        modularity=0.25,
        skipped=["fts"],
    )

    assert to_dict(status)["languages"] == ["python"]
    assert to_dict(postprocess)["communities_detected"] == 5
    json.dumps(to_dict(status), sort_keys=True)
    json.dumps(to_dict(postprocess), sort_keys=True)


def test_project_index_metadata_preserves_created_at_and_records_git_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    _init_git_repo(repo)
    db = tmp_path / "index.db"

    index = ProjectIndex(db)
    try:
        index.initialize_schema()
        assert index.metadata(raise_if_empty=False)["schema_version"] == "csegraph-sqlite-v5"

        index.set_metadata(str(repo), "small")
        first = index.metadata()
        index.set_metadata(str(repo), "medium")
        second = index.metadata()
    finally:
        index.close()

    assert first["root_dir"] == str(repo)
    assert second["active_profile"] == "medium"
    assert second["created_at"] == first["created_at"]
    assert second["built_branch"]
    assert len(second["built_commit"]) == 12


def test_json_dumps_is_stable_and_handles_none() -> None:
    assert json_dumps(None) == "{}"
    assert json_dumps({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'


def test_git_head_state_and_run_git_handle_git_and_non_git_repos(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    _init_git_repo(repo)

    branch, commit = git_head_state(str(repo))
    assert branch is not None
    assert branch != "HEAD"
    assert commit is not None
    assert len(commit) == 12
    assert _run_git(str(repo), "definitely-not-a-real-git-command") is None

    non_git = tmp_path / "plain"
    non_git.mkdir()
    assert git_head_state(str(non_git)) == (None, None)


def test_status_epoch_and_warning_helpers(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    _init_git_repo(repo)
    branch, commit = git_head_state(str(repo))
    assert commit is not None

    assert _epoch_to_iso("0") == "1970-01-01T00:00:00"
    assert _epoch_to_iso("not-a-timestamp") is None

    warnings = _build_warnings(
        {
            "schema_version": "csegraph-sqlite-v5",
            "built_branch": branch,
            "built_commit": "000000000000",
        },
        str(repo),
        current_branch=branch,
        current_commit=commit,
    )

    assert warnings
    assert any(commit in warning for warning in warnings)


def test_status_service_reports_parse_errors_and_current_git_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "valid.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    (repo / "broken.py").write_text("def broken(]\n", encoding="utf-8")
    _init_git_repo(repo)
    db = tmp_path / "index.db"

    IndexService(db).index(str(repo), profile="small")
    result = StatusService(db).status(verbose=True)

    assert result.command == "status"
    assert result.parse_error_count > 0
    assert "broken.py" in result.parse_errors
    assert result.current_branch is not None
    assert result.current_branch != "HEAD"
    assert result.current_commit is not None


def test_postprocess_service_rebuilds_fts_and_communities(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_repo(repo)
    db = tmp_path / "index.db"
    IndexService(db).index(str(repo), profile="small")

    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM lexical_index")
        conn.commit()

    result = PostprocessService(db).postprocess()

    assert result.command == "postprocess"
    assert result.fts_entries > 0
    assert result.communities_detected > 0

    with sqlite3.connect(db) as conn:
        lexical_rows = conn.execute("SELECT COUNT(*) FROM lexical_index").fetchone()[0]
        community_rows = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE community_id IS NOT NULL"
        ).fetchone()[0]

    assert lexical_rows == result.fts_entries
    assert community_rows > 0


def test_status_metadata_clears_stale_git_fields_when_repo_is_not_git(tmp_path: Path) -> None:
    git_repo = tmp_path / "git_repo"
    _write_repo(git_repo)
    _init_git_repo(git_repo)
    db = tmp_path / "index.db"
    IndexService(db).index(str(git_repo), profile="small")
    assert StatusService(db).status().built_commit is not None

    plain_repo = tmp_path / "plain_repo"
    _write_repo(plain_repo)
    with patch("csegraph_core.index.repository.git_head_state", return_value=(None, None)):
        IndexService(db).index(str(plain_repo), profile="small")

    result = StatusService(db).status()
    assert result.built_branch is None
    assert result.built_commit is None
