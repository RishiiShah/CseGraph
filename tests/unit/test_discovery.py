import subprocess
import pytest

from csegraph._core.discovery import is_discoverable_rel_path, iter_discoverable_rel_paths
from csegraph._core.ignore import load_ignore_filter, recurse_submodules_enabled
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


def _git_repo_with_submodule(tmp_path):
    lib_repo = tmp_path / "lib"
    lib_repo.mkdir()
    _git(lib_repo, "init")
    _git(lib_repo, "config", "user.email", "test@test.com")
    _git(lib_repo, "config", "user.name", "Test")
    (lib_repo / "util.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    _git(lib_repo, "add", "util.py")
    _git(lib_repo, "commit", "-m", "lib initial")

    parent = tmp_path / "parent"
    parent.mkdir()
    _git(parent, "init")
    _git(parent, "config", "user.email", "test@test.com")
    _git(parent, "config", "user.name", "Test")
    (parent / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
    _git(parent, "add", "main.py")
    _git(parent, "commit", "-m", "parent initial")
    _git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(lib_repo),
        "lib",
    )
    _git(parent, "commit", "-m", "add lib submodule")
    return parent


def test_submodule_files_discovered_by_default(tmp_path):
    parent = _git_repo_with_submodule(tmp_path)
    ignore = load_ignore_filter(parent)
    rels = list(iter_discoverable_rel_paths(parent, ignore=ignore))
    assert "main.py" in rels
    assert "lib/util.py" in rels


def test_submodule_files_skipped_when_recurse_disabled(tmp_path):
    parent = _git_repo_with_submodule(tmp_path)
    ignore = load_ignore_filter(parent, recurse_submodules=False)
    rels = list(iter_discoverable_rel_paths(parent, ignore=ignore))
    assert "main.py" in rels
    assert not any(path.startswith("lib/") for path in rels)


def test_recurse_submodules_env_override(monkeypatch):
    monkeypatch.delenv("CSEGRAPH_RECURSE_SUBMODULES", raising=False)
    assert recurse_submodules_enabled() is True
    monkeypatch.setenv("CSEGRAPH_RECURSE_SUBMODULES", "0")
    assert recurse_submodules_enabled() is False
    monkeypatch.setenv("CSEGRAPH_RECURSE_SUBMODULES", "yes")
    assert recurse_submodules_enabled() is True


def test_svn_list_paths_used_when_no_git(monkeypatch, tmp_path):
    (tmp_path / ".svn").mkdir()
    (tmp_path / "versioned.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "local_only.py").write_text("y = 2\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["svn", "list"]:
            return subprocess.CompletedProcess(cmd, 0, "versioned.py\n", "")
        raise FileNotFoundError

    monkeypatch.setattr("csegraph._core.vcs.subprocess.run", fake_run)
    monkeypatch.setattr("csegraph._core.ignore._git_root", lambda _root: None)

    ignore = load_ignore_filter(tmp_path)
    assert ignore.svn_repo
    assert not ignore.git_repo
    rels = list(iter_discoverable_rel_paths(tmp_path, ignore=ignore))
    assert "versioned.py" in rels
    assert "local_only.py" not in rels


def test_git_takes_precedence_over_svn_marker(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / ".svn").mkdir()
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "a.py")

    ignore = load_ignore_filter(tmp_path)
    assert ignore.git_repo
    assert ignore.vcs == "git"
    assert not ignore.svn_repo
