"""Integration tests for csegraph status and postprocess commands."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from csegraph._core.core.models import to_dict
from csegraph._core.index.services import IndexService
from csegraph._core.postprocess import PostprocessService, _read_source_slice
from csegraph._core.status import StatusService


def _run_cli(*args: str) -> tuple[int, str, str]:
    """Run csegraph._cli and return (exit_code, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "-m", "csegraph._cli", *args],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def _scratch_path(repo: Path, name: str | Path) -> Path:
    return repo / ".scratch" / "csegraph" / Path(name)


def _make_repo(tmp_path: Path, files: dict[str, str]) -> tuple[str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, content in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    db = str(_scratch_path(repo, "index.db"))
    IndexService(db).index(str(repo), profile="small")
    return str(repo), db


def _make_git_repo(tmp_path: Path, files: dict[str, str]) -> tuple[str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, content in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com"}
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True, env=env)
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=True, env=env)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True, env=env)

    db = str(_scratch_path(repo, "index.db"))
    IndexService(db).index(str(repo), profile="small")
    return str(repo), db


SAMPLE_FILES = {
    "a.py": "from b import helper\n\ndef main():\n    helper()\n",
    "b.py": "def helper():\n    pass\n",
    "c.js": "function standalone() { return 1; }\n",
}


class TestStatusService:
    def test_status_json_fields(self, tmp_path):
        _repo, db = _make_repo(tmp_path, SAMPLE_FILES)
        result = StatusService(db).status()
        assert result.command == "status"
        assert result.total_nodes > 0
        assert result.total_edges > 0
        assert result.total_files == 3
        assert "python" in result.languages
        assert result.schema_version == "csegraph-sqlite-v5"
        assert result.active_profile == "small"
        assert result.updated_at is not None

    def test_status_serializable(self, tmp_path):
        _repo, db = _make_repo(tmp_path, SAMPLE_FILES)
        result = StatusService(db).status()
        payload = to_dict(result)
        serialized = json.dumps(payload, sort_keys=True)
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["total_files"] == 3
        assert "python" in parsed["languages"]

    def test_status_text_output_contains_expected_labels(self, tmp_path):
        from csegraph._cli.renderer import render_status_summary

        _repo, db = _make_repo(tmp_path, SAMPLE_FILES)
        result = StatusService(db).status()
        text = render_status_summary(to_dict(result))
        assert "Nodes:" in text
        assert "Edges:" in text
        assert "Files:" in text
        assert "Languages:" in text
        assert "Schema:" in text
        assert "Last updated:" in text

    def test_status_missing_db(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        db = str(_scratch_path(repo, Path("nonexistent") / "index.db"))
        with pytest.raises(ValueError, match="No csegraph index found"):
            StatusService(db).status()

    def test_status_empty_db(self, tmp_path):
        # Create an empty DB file (no schema)
        repo = tmp_path / "repo"
        repo.mkdir()
        db = _scratch_path(repo, "empty.db")
        db.parent.mkdir(parents=True, exist_ok=True)
        db.touch()
        with pytest.raises(ValueError, match="No csegraph index found"):
            StatusService(str(db)).status()

    def test_status_languages_sorted(self, tmp_path):
        _repo, db = _make_repo(tmp_path, SAMPLE_FILES)
        result = StatusService(db).status()
        assert result.languages == sorted(result.languages)

    def test_status_verbose_false_no_parse_errors(self, tmp_path):
        _repo, db = _make_repo(tmp_path, SAMPLE_FILES)
        result = StatusService(db).status(verbose=False)
        assert result.parse_errors == {}

    def test_status_verbose_true_includes_parse_errors(self, tmp_path):
        # Create a repo with an invalid Python file
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "valid.py").write_text("def f(): pass\n")
        (repo / "invalid.py").write_text("def f( ]\n")  # Syntax error

        db = str(_scratch_path(repo, "index.db"))
        IndexService(db).index(str(repo), profile="small")

        result = StatusService(db).status(verbose=True)
        assert result.parse_error_count > 0
        assert result.parse_errors is not None
        # Should have at least the invalid file
        assert len(result.parse_errors) > 0
        assert "invalid.py" in result.parse_errors


class TestStatusRendering:
    def test_render_status_verbose_includes_parse_errors(self, tmp_path):
        from csegraph._cli.renderer import render_status_summary

        # Create a repo with an invalid Python file
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "valid.py").write_text("def f(): pass\n")
        (repo / "invalid.py").write_text("def f( ]\n")  # Syntax error

        db = str(_scratch_path(repo, "index.db"))
        IndexService(db).index(str(repo), profile="small")

        result = StatusService(db).status(verbose=True)
        payload = to_dict(result)
        text = render_status_summary(payload)
        if result.parse_errors:
            assert "Parse errors:" in text
            assert "invalid.py" in text


class TestStatusGitMetadata:
    def test_built_branch_and_commit_present(self, tmp_path):
        _repo, db = _make_git_repo(tmp_path, {"a.py": "def f(): pass\n"})
        result = StatusService(db).status()
        assert result.built_branch is not None
        assert result.built_commit is not None
        assert len(result.built_commit) == 12

    def test_branch_mismatch_warning(self, tmp_path):
        repo, db = _make_git_repo(tmp_path, {"a.py": "def f(): pass\n"})
        env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
               "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com"}
        subprocess.run(
            ["git", "checkout", "-b", "other-branch"],
            cwd=repo, capture_output=True, check=True, env=env,
        )
        result = StatusService(db).status()
        branch_warnings = [w for w in result.warnings if "branch" in w.lower()]
        assert len(branch_warnings) == 1
        assert "other-branch" in branch_warnings[0]

    def test_no_warnings_when_matching(self, tmp_path):
        _repo, db = _make_git_repo(tmp_path, {"a.py": "def f(): pass\n"})
        result = StatusService(db).status()
        branch_warnings = [w for w in result.warnings if "branch" in w.lower()]
        assert len(branch_warnings) == 0

    def test_stale_metadata_cleared_on_non_git_repo(self, tmp_path):
        repo, db = _make_git_repo(tmp_path, {"a.py": "def f(): pass\n"})
        result1 = StatusService(db).status()
        assert result1.built_branch is not None
        assert result1.built_commit is not None

        new_repo = tmp_path / "non_git_repo"
        new_repo.mkdir()
        (new_repo / "b.py").write_text("def g(): pass\n")

        with patch("csegraph._core.index.repository.git_head_state", return_value=(None, None)):
            IndexService(db).index(str(new_repo), profile="small")
        result2 = StatusService(db).status()
        assert result2.built_branch is None
        assert result2.built_commit is None


class TestPostprocessService:
    def test_postprocess_fts_rebuild(self, tmp_path):
        _repo, db = _make_repo(tmp_path, SAMPLE_FILES)

        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM lexical_index")
        conn.commit()
        count_before = conn.execute("SELECT count(*) FROM lexical_index").fetchone()[0]
        conn.close()
        assert count_before == 0

        result = PostprocessService(db).postprocess(no_communities=True)
        assert result.command == "postprocess"
        assert result.fts_entries > 0
        assert result.skipped == ["communities"]

        conn = sqlite3.connect(db)
        count_after = conn.execute("SELECT count(*) FROM lexical_index").fetchone()[0]
        conn.close()
        assert count_after == result.fts_entries

    def test_postprocess_communities(self, tmp_path):
        _repo, db = _make_repo(tmp_path, SAMPLE_FILES)
        result = PostprocessService(db).postprocess(no_fts=True)
        assert result.communities_detected > 0
        assert result.skipped == ["fts"]

        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT community_id FROM nodes WHERE type IN ('file','function','class','method')"
        ).fetchall()
        conn.close()
        assert any(r[0] is not None for r in rows)

    def test_postprocess_both_skipped(self, tmp_path):
        _repo, db = _make_repo(tmp_path, SAMPLE_FILES)
        result = PostprocessService(db).postprocess(no_fts=True, no_communities=True)
        assert result.fts_entries == 0
        assert result.communities_detected == 0
        assert result.skipped == ["fts", "communities"]

    def test_postprocess_serializable(self, tmp_path):
        _repo, db = _make_repo(tmp_path, SAMPLE_FILES)
        result = PostprocessService(db).postprocess()
        payload = to_dict(result)
        serialized = json.dumps(payload, sort_keys=True)
        assert isinstance(serialized, str)

    def test_postprocess_full(self, tmp_path):
        _repo, db = _make_repo(tmp_path, SAMPLE_FILES)
        result = PostprocessService(db).postprocess()
        assert result.fts_entries > 0
        assert result.communities_detected > 0
        assert result.skipped == []


class TestPostprocessPreflight:
    def test_postprocess_missing_db_raises(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        db = str(_scratch_path(repo, Path("nonexistent") / "index.db"))
        with pytest.raises(ValueError, match="No csegraph index found"):
            PostprocessService(db).postprocess()

    def test_postprocess_missing_db_not_created(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        db = _scratch_path(repo, Path("nonexistent") / "index.db")
        try:
            PostprocessService(str(db)).postprocess()
        except ValueError:
            pass
        assert not db.exists(), "postprocess should not create DB on error"


class TestReadSourceSliceTraversal:
    def test_path_traversal_returns_empty(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("secret = 'leaked'\n", encoding="utf-8")
        result = _read_source_slice(str(repo), "../outside.py", "python", 1, 1)
        assert result == ""

    def test_valid_path_returns_source(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "valid.py").write_text("def f(): pass\n", encoding="utf-8")
        result = _read_source_slice(str(repo), "valid.py", "python", 1, 1)
        assert result != ""


class TestPostprocessRenderer:
    def test_render_postprocess(self, tmp_path):
        from csegraph._cli.renderer import render_postprocess_summary

        _repo, db = _make_repo(tmp_path, SAMPLE_FILES)
        result = PostprocessService(db).postprocess()
        text = render_postprocess_summary(to_dict(result))
        assert "Post-processing:" in text
        assert "FTS entries" in text
        assert "communities" in text


class TestCLIStatus:
    def test_cli_status_json(self, tmp_path):
        repo, db = _make_repo(tmp_path, SAMPLE_FILES)
        code, stdout, stderr = _run_cli("status", "--repo", repo, "--db", db, "--json")
        assert code == 0
        payload = json.loads(stdout)
        assert payload["command"] == "status"
        assert payload["total_files"] == 3
        assert "parse_errors" in payload

    def test_cli_status_verbose(self, tmp_path):
        # Create a repo with an invalid file
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "valid.py").write_text("def f(): pass\n")
        (repo / "invalid.py").write_text("def f( ]\n")
        db = str(_scratch_path(repo, "index.db"))
        IndexService(db).index(str(repo), profile="small")

        code, stdout, stderr = _run_cli("status", "--repo", str(repo), "--db", db, "--verbose")
        assert code == 0
        assert "Parse errors:" in stdout
        assert "invalid.py" in stdout

    def test_cli_status_missing_db(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        db = _scratch_path(repo, "nonexistent.db")
        code, stdout, stderr = _run_cli("status", "--repo", str(repo), "--db", str(db))
        assert code == 1
        payload = json.loads(stderr)
        assert payload["error"] == "No csegraph index found. Run csegraph index first."

    def test_cli_status_empty_db(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        db = _scratch_path(repo, "empty.db")
        db.parent.mkdir(parents=True, exist_ok=True)
        db.touch()
        code, stdout, stderr = _run_cli("status", "--repo", str(repo), "--db", str(db))
        assert code == 1
        payload = json.loads(stderr)
        assert payload["error"] == "No csegraph index found. Run csegraph index first."


class TestCLIPostprocess:
    def test_cli_postprocess_json(self, tmp_path):
        repo, db = _make_repo(tmp_path, SAMPLE_FILES)
        code, stdout, stderr = _run_cli("postprocess", "--repo", repo, "--db", db, "--json")
        assert code == 0
        payload = json.loads(stdout)
        assert payload["command"] == "postprocess"
        assert "fts_entries" in payload
        assert "communities_detected" in payload

    def test_cli_postprocess_no_fts_no_communities(self, tmp_path):
        repo, db = _make_repo(tmp_path, SAMPLE_FILES)
        code, stdout, stderr = _run_cli(
            "postprocess", "--repo", repo, "--db", db, "--no-fts", "--no-communities", "--json"
        )
        assert code == 0
        payload = json.loads(stdout)
        assert payload["fts_entries"] == 0
        assert payload["communities_detected"] == 0
        assert "fts" in payload["skipped"]
        assert "communities" in payload["skipped"]

    def test_cli_postprocess_missing_db(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        db = _scratch_path(repo, "nonexistent.db")
        code, stdout, stderr = _run_cli("postprocess", "--repo", str(repo), "--db", str(db))
        assert code == 1
        payload = json.loads(stderr)
        assert payload["error"] == "No csegraph index found. Run csegraph index first."

    def test_cli_postprocess_empty_db(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        db = _scratch_path(repo, "empty.db")
        db.parent.mkdir(parents=True, exist_ok=True)
        db.touch()
        code, stdout, stderr = _run_cli("postprocess", "--repo", str(repo), "--db", str(db))
        assert code == 1
        payload = json.loads(stderr)
        assert payload["error"] == "No csegraph index found. Run csegraph index first."
