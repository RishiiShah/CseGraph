"""Tests for review evaluation harness."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from csegraph._core.core.models import to_dict
from csegraph._core.graph.review_eval import ReviewEvalService
from csegraph._core.index.services import IndexService


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


def _get_node_id(db: str, name: str) -> str:
    from csegraph._core.index.repository import ProjectIndex
    idx = ProjectIndex(db)
    try:
        idx.initialize_schema()
        row = idx.conn.execute(
            "SELECT id FROM nodes WHERE name = ? AND type IN ('class','function','method')",
            (name,),
        ).fetchone()
        return row["id"] if row else name
    finally:
        idx.close()


class TestReviewEval:
    def test_perfect_detection(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)

        (repo / "core.py").write_text("def target_fn():\n    pass\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")

        (repo / "core.py").write_text("def target_fn():\n    return 1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "modify")

        db = _index_repo(tmp_path, repo)
        node_id = _get_node_id(db, "target_fn")

        result = ReviewEvalService(db).evaluate(
            ground_truth_ids=[node_id],
            base_ref="HEAD~1",
            risk_threshold="low",
        )

        assert result.command == "review-eval"
        assert result.overall_recall > 0.0
        assert node_id not in result.missed_symbols

    def test_missed_symbol(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)

        (repo / "a.py").write_text("def changed():\n    pass\n", encoding="utf-8")
        (repo / "b.py").write_text("def not_changed():\n    pass\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")

        (repo / "a.py").write_text("def changed():\n    return 1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "modify a only")

        db = _index_repo(tmp_path, repo)
        not_changed_id = _get_node_id(db, "not_changed")

        result = ReviewEvalService(db).evaluate(
            ground_truth_ids=[not_changed_id],
            base_ref="HEAD~1",
        )

        assert not_changed_id in result.missed_symbols

    def test_empty_ground_truth(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)

        (repo / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")

        (repo / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "modify")

        db = _index_repo(tmp_path, repo)
        result = ReviewEvalService(db).evaluate(
            ground_truth_ids=[],
            base_ref="HEAD~1",
        )

        assert result.ground_truth_count == 0
        assert result.overall_recall == 0.0

    def test_bad_ids_warn(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)

        (repo / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")

        db = _index_repo(tmp_path, repo)
        result = ReviewEvalService(db).evaluate(
            ground_truth_ids=["nonexistent_id_123"],
            base_ref="HEAD",
        )

        assert any("nonexistent_id_123" in w for w in result.warnings)

    def test_risk_threshold_high(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)

        (repo / "core.py").write_text("def fn():\n    pass\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")

        (repo / "core.py").write_text("def fn():\n    return 1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "modify")

        db = _index_repo(tmp_path, repo)
        node_id = _get_node_id(db, "fn")

        result_high = ReviewEvalService(db).evaluate(
            ground_truth_ids=[node_id],
            base_ref="HEAD~1",
            risk_threshold="high",
        )
        result_low = ReviewEvalService(db).evaluate(
            ground_truth_ids=[node_id],
            base_ref="HEAD~1",
            risk_threshold="low",
        )

        assert result_low.overall_recall >= result_high.overall_recall

    def test_serializable(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)

        (repo / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")

        (repo / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "modify")

        db = _index_repo(tmp_path, repo)
        result = ReviewEvalService(db).evaluate(
            ground_truth_ids=[],
            base_ref="HEAD~1",
        )
        payload = to_dict(result)
        assert isinstance(json.dumps(payload), str)
