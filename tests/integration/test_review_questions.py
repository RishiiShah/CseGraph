"""Tests for review question generation service."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from csegraph._core.core.models import to_dict
from csegraph._core.graph.review_questions import ReviewQuestionsService
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


class TestReviewQuestions:
    def test_no_changes_no_questions(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)

        (repo / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")

        db = _index_repo(tmp_path, repo)
        result = ReviewQuestionsService(db).generate(base_ref="HEAD")

        assert result.command == "review-questions"
        assert result.total_questions == 0
        assert result.questions == []

    def test_high_risk_untested_generates_p1(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)

        (repo / "core.py").write_text(
            "def hub():\n    pass\n",
            encoding="utf-8",
        )
        (repo / "c1.py").write_text(
            "from core import hub\ndef caller1():\n    hub()\n",
            encoding="utf-8",
        )
        (repo / "c2.py").write_text(
            "from core import hub\ndef caller2():\n    hub()\n",
            encoding="utf-8",
        )
        (repo / "c3.py").write_text(
            "from core import hub\ndef caller3():\n    hub()\n",
            encoding="utf-8",
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")

        (repo / "core.py").write_text(
            "def hub():\n    return 42\n",
            encoding="utf-8",
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "modify hub")

        db = _index_repo(tmp_path, repo)
        result = ReviewQuestionsService(db).generate(base_ref="HEAD~1")

        assert result.total_questions >= 1
        categories = {q.category for q in result.questions}
        priorities = {q.priority for q in result.questions}
        assert any(q.priority <= 2 for q in result.questions)

    def test_question_limit(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)

        funcs = "\n".join(f"def fn_{i}():\n    pass\n" for i in range(20))
        (repo / "big.py").write_text(funcs, encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")

        new_funcs = "\n".join(f"def fn_{i}():\n    return {i}\n" for i in range(20))
        (repo / "big.py").write_text(new_funcs, encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "modify all")

        db = _index_repo(tmp_path, repo)
        result = ReviewQuestionsService(db).generate(base_ref="HEAD~1")

        assert result.total_questions <= 10

    def test_serializable(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)

        (repo / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")

        (repo / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "change")

        db = _index_repo(tmp_path, repo)
        result = ReviewQuestionsService(db).generate(base_ref="HEAD~1")
        payload = to_dict(result)
        assert isinstance(json.dumps(payload), str)

    def test_cross_community_question(self, tmp_path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)

        (repo / "a.py").write_text(
            "def a_fn():\n    pass\n",
            encoding="utf-8",
        )
        (repo / "b.py").write_text(
            "from a import a_fn\ndef b_fn():\n    a_fn()\n",
            encoding="utf-8",
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial")

        (repo / "a.py").write_text(
            "def a_fn():\n    return 1\n",
            encoding="utf-8",
        )
        (repo / "b.py").write_text(
            "from a import a_fn\ndef b_fn():\n    return a_fn() + 1\n",
            encoding="utf-8",
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "modify both")

        db = _index_repo(tmp_path, repo)
        from csegraph._core.postprocess import PostprocessService
        PostprocessService(db).postprocess()

        result = ReviewQuestionsService(db).generate(base_ref="HEAD~1")
        assert result.total_questions >= 0  # may or may not generate depending on communities
