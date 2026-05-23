"""Tests for change detection service."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from csegraph_core.core.models import to_dict
from csegraph_core.graph.change_detection import (
    ChangeDetectionService,
    DiffRegion,
    _compute_risk,
    _parse_diff,
)
from csegraph_core.index.services import IndexService
from csegraph_core.postprocess import PostprocessService


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=True,
    )


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")


def _index_repo(tmp_path: Path, repo: Path) -> str:
    db = str(tmp_path / "index.db")
    IndexService(db).index(str(repo), profile="small")
    return db


class TestDiffParser:
    def test_modified_file(self):
        diff = "\n".join([
            "diff --git a/foo.py b/foo.py",
            "index abc1234..def5678 100644",
            "--- a/foo.py",
            "+++ b/foo.py",
            "@@ -3,2 +3,4 @@ def foo():",
            "+    new_line_1",
            "+    new_line_2",
        ])
        regions = _parse_diff(diff)
        assert len(regions) == 1
        assert regions[0].path == "foo.py"
        assert regions[0].changed_lines == [(3, 6)]
        assert not regions[0].is_new_file
        assert not regions[0].is_deleted_file

    def test_new_file(self):
        diff = "\n".join([
            "diff --git a/new.py b/new.py",
            "new file mode 100644",
            "index 0000000..abc1234",
            "--- /dev/null",
            "+++ b/new.py",
            "@@ -0,0 +1,5 @@",
            "+line1",
        ])
        regions = _parse_diff(diff)
        assert len(regions) == 1
        assert regions[0].is_new_file
        assert regions[0].changed_lines == [(1, 5)]

    def test_deleted_file(self):
        diff = "\n".join([
            "diff --git a/old.py b/old.py",
            "deleted file mode 100644",
            "index abc1234..0000000",
            "--- a/old.py",
            "+++ /dev/null",
            "@@ -1,3 +0,0 @@",
            "-line1",
        ])
        regions = _parse_diff(diff)
        assert len(regions) == 1
        assert regions[0].is_deleted_file
        assert regions[0].changed_lines == []

    def test_multiple_hunks(self):
        diff = "\n".join([
            "diff --git a/foo.py b/foo.py",
            "--- a/foo.py",
            "+++ b/foo.py",
            "@@ -3 +3 @@",
            "+changed",
            "@@ -10,2 +10,3 @@",
            "+added",
        ])
        regions = _parse_diff(diff)
        assert len(regions) == 1
        assert regions[0].changed_lines == [(3, 3), (10, 12)]

    def test_multiple_files(self):
        diff = "\n".join([
            "diff --git a/a.py b/a.py",
            "--- a/a.py",
            "+++ b/a.py",
            "@@ -1 +1 @@",
            "+x",
            "diff --git a/b.py b/b.py",
            "--- a/b.py",
            "+++ b/b.py",
            "@@ -5,3 +5,4 @@",
            "+y",
        ])
        regions = _parse_diff(diff)
        assert len(regions) == 2
        assert regions[0].path == "a.py"
        assert regions[1].path == "b.py"

    def test_empty_diff(self):
        assert _parse_diff("") == []

    def test_subdirectory_path(self):
        diff = "\n".join([
            "diff --git a/src/lib/utils.py b/src/lib/utils.py",
            "--- a/src/lib/utils.py",
            "+++ b/src/lib/utils.py",
            "@@ -1,2 +1,3 @@",
            "+new",
        ])
        regions = _parse_diff(diff)
        assert len(regions) == 1
        assert regions[0].path == "src/lib/utils.py"


class TestRiskScoring:
    def test_high_risk_many_callers_no_tests(self):
        score, level, factors = _compute_risk(12, 3, False)
        assert level == "high"
        assert "12 caller(s)" in factors
        assert "no test coverage" in factors

    def test_low_risk_leaf_with_tests(self):
        score, level, factors = _compute_risk(0, 0, True)
        assert level == "low"
        assert score == 0.0
        assert factors == []

    def test_untested_leaf_is_medium(self):
        score, level, _ = _compute_risk(0, 0, False)
        assert level == "medium"
        assert score == 0.3

    def test_many_callers_with_tests_is_medium(self):
        score, level, _ = _compute_risk(10, 0, True)
        assert level == "medium"
        assert score == 0.5

    def test_score_saturates(self):
        score_10, _, _ = _compute_risk(10, 5, False)
        score_100, _, _ = _compute_risk(100, 50, False)
        assert score_10 == score_100 == 1.0

    def test_cross_community_contributes(self):
        score_no_cc, _, _ = _compute_risk(0, 0, False)
        score_with_cc, _, _ = _compute_risk(0, 3, False)
        assert score_with_cc > score_no_cc


class TestAnalyzeRegions:
    def test_new_file_all_symbols_changed(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "mod.py").write_text(
            "def alpha():\n    pass\n\ndef beta():\n    alpha()\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        regions = [DiffRegion(path="mod.py", changed_lines=[], is_new_file=True)]
        result = ChangeDetectionService(db).analyze_regions(regions)

        assert result.command == "detect-changes"
        assert result.total_changed_symbols >= 2
        names = {s.name for s in result.high_risk + result.medium_risk + result.low_risk}
        assert "alpha" in names
        assert "beta" in names

    def test_partial_change_targets_function(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "mod.py").write_text(
            "def first():\n    pass\n\ndef second():\n    pass\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)

        regions = [DiffRegion(path="mod.py", changed_lines=[(4, 5)])]
        result = ChangeDetectionService(db).analyze_regions(regions)

        names = {s.name for s in result.high_risk + result.medium_risk + result.low_risk}
        assert "second" in names
        assert "first" not in names

    def test_deleted_file_skipped(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "keep.py").write_text("def keep(): pass\n", encoding="utf-8")
        db = _index_repo(tmp_path, repo)

        regions = [DiffRegion(path="gone.py", changed_lines=[], is_deleted_file=True)]
        result = ChangeDetectionService(db).analyze_regions(regions)
        assert result.total_changed_symbols == 0

    def test_unindexed_file_warns(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "known.py").write_text("def f(): pass\n", encoding="utf-8")
        db = _index_repo(tmp_path, repo)

        regions = [DiffRegion(path="unknown.py", changed_lines=[(1, 5)])]
        result = ChangeDetectionService(db).analyze_regions(regions)
        assert any("unknown.py" in w for w in result.warnings)

    def test_empty_regions(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        db = _index_repo(tmp_path, repo)

        result = ChangeDetectionService(db).analyze_regions([])
        assert result.total_changed_symbols == 0
        assert result.summary.startswith("0 changed symbol(s)")

    def test_serializable(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "f.py").write_text("def f(): pass\n", encoding="utf-8")
        db = _index_repo(tmp_path, repo)
        regions = [DiffRegion(path="f.py", changed_lines=[], is_new_file=True)]
        result = ChangeDetectionService(db).analyze_regions(regions)
        payload = to_dict(result)
        assert isinstance(json.dumps(payload), str)

    def test_communities_affected_count(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text(
            "def a1(): a2()\ndef a2(): pass\n",
            encoding="utf-8",
        )
        (repo / "b.py").write_text(
            "def b1(): b2()\ndef b2(): pass\n",
            encoding="utf-8",
        )
        db = _index_repo(tmp_path, repo)
        PostprocessService(db).postprocess()

        regions = [
            DiffRegion(path="a.py", changed_lines=[], is_new_file=True),
            DiffRegion(path="b.py", changed_lines=[], is_new_file=True),
        ]
        result = ChangeDetectionService(db).analyze_regions(regions)
        assert result.communities_affected >= 1


class TestChangeDetectionGit:
    def test_detect_changes_modified_function(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)

        (repo / "main.py").write_text(
            "def hub():\n    leaf()\n\ndef leaf():\n    pass\n",
            encoding="utf-8",
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")

        (repo / "main.py").write_text(
            "def hub():\n    leaf()\n\ndef leaf():\n    return 42\n",
            encoding="utf-8",
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "modify leaf")

        db = _index_repo(tmp_path, repo)
        result = ChangeDetectionService(db).detect_changes(base_ref="HEAD~1")

        assert result.command == "detect-changes"
        assert result.base_ref == "HEAD~1"
        assert "main.py" in result.changed_files
        assert result.total_changed_symbols >= 1

        all_syms = result.high_risk + result.medium_risk + result.low_risk
        names = {s.name for s in all_syms}
        assert "leaf" in names

    def test_new_file_in_diff(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)

        (repo / "old.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")

        (repo / "new_mod.py").write_text(
            "def fresh():\n    pass\n",
            encoding="utf-8",
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "add module")

        db = _index_repo(tmp_path, repo)
        result = ChangeDetectionService(db).detect_changes(base_ref="HEAD~1")

        all_syms = result.high_risk + result.medium_risk + result.low_risk
        names = {s.name for s in all_syms}
        assert "fresh" in names

    def test_no_changes(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)

        (repo / "a.py").write_text("def a(): pass\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")

        db = _index_repo(tmp_path, repo)
        result = ChangeDetectionService(db).detect_changes(base_ref="HEAD")

        assert result.total_changed_symbols == 0
        assert result.high_risk == []

    def test_risk_ordering(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)

        (repo / "core.py").write_text(
            "def hub_fn():\n    pass\n\ndef leaf_fn():\n    pass\n",
            encoding="utf-8",
        )
        (repo / "caller1.py").write_text(
            "from core import hub_fn\ndef c1(): hub_fn()\n",
            encoding="utf-8",
        )
        (repo / "caller2.py").write_text(
            "from core import hub_fn\ndef c2(): hub_fn()\n",
            encoding="utf-8",
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")

        (repo / "core.py").write_text(
            "def hub_fn():\n    return 1\n\ndef leaf_fn():\n    return 2\n",
            encoding="utf-8",
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "modify both")

        db = _index_repo(tmp_path, repo)
        result = ChangeDetectionService(db).detect_changes(base_ref="HEAD~1")

        all_syms = result.high_risk + result.medium_risk + result.low_risk
        hub = next((s for s in all_syms if s.name == "hub_fn"), None)
        leaf = next((s for s in all_syms if s.name == "leaf_fn"), None)

        if hub and leaf:
            assert hub.risk_score >= leaf.risk_score
