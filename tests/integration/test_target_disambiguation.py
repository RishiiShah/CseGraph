"""Target disambiguation returns a compact card instead of guessing."""

from __future__ import annotations

from pathlib import Path

import pytest

from csegraph import ContextService, IndexService
from csegraph._core.core.serializer import to_dict


def _write_ambiguous_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    (repo / "a.py").write_text("def build():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text("def build():\n    return 2\n", encoding="utf-8")


def _write_unique_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    (repo / "a.py").write_text("def build_a():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text("def build_b():\n    return 2\n", encoding="utf-8")


def test_ambiguous_target_returns_disambiguation_card(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_ambiguous_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="change build behavior",
        target="build",
        profile="small",
    )
    payload = to_dict(context)

    assert payload["target_resolution"] == "ambiguous"
    assert len(payload["target_candidates"]) >= 2
    assert payload["nodes"] == []
    assert any(a["action"] == "resolve_target" for a in payload["next_actions"])
    assert any("matched" in w for w in payload["warnings"])


def test_unique_symbol_name_still_resolves(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_unique_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="change build_a",
        target="build_a",
        profile="small",
    )
    payload = to_dict(context)

    assert payload["target_resolution"] == "resolved"
    assert payload["nodes"]


@pytest.mark.parametrize("target", ["a.py", "./a.py"])
def test_repo_relative_target_resolves_regardless_of_cwd(tmp_path, monkeypatch, target):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_unique_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    # Change CWD to a temp directory outside the repo
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    monkeypatch.chdir(outside_dir)

    # Resolve target using a repo-relative path.
    context = ContextService(db_path).build_context(
        task="explain this",
        target=target,
        profile="small",
    )
    payload = to_dict(context)
    assert payload["target_resolution"] == "resolved"
    assert any(node["name"] == "a.py" for node in payload["nodes"])
