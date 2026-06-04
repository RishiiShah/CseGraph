import subprocess

import pytest

from csegraph._core.discovery import is_discoverable_rel_path, iter_discoverable_rel_paths
from csegraph._core.ignore import load_ignore_filter
from csegraph._core.languages.registry import registry


def _git(repo, *args):
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_git_repo_uses_ls_files_not_untracked(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "local_ref.py").write_text("y = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.py")

    ignore = load_ignore_filter(tmp_path)
    rels = list(iter_discoverable_rel_paths(tmp_path, ignore=ignore))

    assert "tracked.py" in rels
    assert "local_ref.py" not in rels


def test_staged_uncommitted_file_is_discoverable(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "wip.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "wip.py")

    ignore = load_ignore_filter(tmp_path)
    assert "wip.py" in list(iter_discoverable_rel_paths(tmp_path, ignore=ignore))


def test_non_git_repo_walks_untracked(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")

    rels = list(iter_discoverable_rel_paths(tmp_path))

    assert "a.py" in rels
    assert "b.py" in rels


def test_csegraphignore_excludes_indexed_path(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "skip.py").write_text("y = 2\n", encoding="utf-8")
    (tmp_path / ".csegraphignore").write_text("skip.py\n", encoding="utf-8")
    _git(tmp_path, "add", ".csegraphignore", "keep.py", "skip.py")

    ignore = load_ignore_filter(tmp_path)
    rels = list(iter_discoverable_rel_paths(tmp_path, ignore=ignore))

    assert "keep.py" in rels
    assert "skip.py" not in rels


def test_registry_iter_files_skips_untracked_in_git_repo(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "indexed.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "ref_only.py").write_text("y = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "indexed.py")

    pairs = list(registry.iter_files(tmp_path))
    paths = {p.name for _, p in pairs}

    assert "indexed.py" in paths
    assert "ref_only.py" not in paths


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("tracked.py", True),
        ("draft.py", False),
    ],
)
def test_is_discoverable_rel_path(tmp_path, rel, expected):
    _git(tmp_path, "init")
    (tmp_path / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "draft.py").write_text("y = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.py")

    ignore = load_ignore_filter(tmp_path)
    assert is_discoverable_rel_path(rel, ignore) is expected
