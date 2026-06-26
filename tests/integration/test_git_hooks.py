"""Integration tests for csegraph git hooks install/uninstall."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from csegraph._core.hooks import (
    HOOK_MARKER,
    HOOK_NAMES,
    find_git_dir,
    install_hooks,
    uninstall_hooks,
)


def _init_git_repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git executable is not available")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        [sys.executable, "-c", "import subprocess; subprocess.run(['git', 'init'], check=True)"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    if not (repo / ".git").exists():
        subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    return repo


class TestFindGitDir:
    def test_finds_git_dir(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        git_dir = find_git_dir(repo)
        assert git_dir.exists()
        assert git_dir.name == ".git"

    def test_raises_if_no_git(self, tmp_path):
        no_git = tmp_path / "norepo"
        no_git.mkdir()
        with pytest.raises(FileNotFoundError):
            find_git_dir(no_git)


class TestInstallHooks:
    def test_installs_all_hooks(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        result = install_hooks(repo)
        assert result.command == "hooks install"
        assert set(result.installed) == set(HOOK_NAMES)
        assert result.skipped == []

        for name in HOOK_NAMES:
            hook = Path(result.hooks_dir) / name
            assert hook.exists()
            content = hook.read_text(encoding="utf-8")
            assert HOOK_MARKER in content
            assert "csegraph refresh" in content
            assert "--changed-from-git" in content

    def test_skips_already_installed(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        install_hooks(repo)
        result = install_hooks(repo)
        assert set(result.skipped) == set(HOOK_NAMES)
        assert result.installed == []

    def test_appends_to_existing_hook(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        hooks_dir = repo / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        existing = hooks_dir / "post-commit"
        existing.write_text("#!/bin/sh\necho existing\n", encoding="utf-8")

        result = install_hooks(repo)
        assert "post-commit" in result.installed
        content = existing.read_text(encoding="utf-8")
        assert "echo existing" in content
        assert HOOK_MARKER in content


class TestUninstallHooks:
    def test_removes_hooks(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        install_hooks(repo)
        result = uninstall_hooks(repo)
        assert set(result.installed) == set(HOOK_NAMES)

        for name in HOOK_NAMES:
            hook = Path(result.hooks_dir) / name
            if hook.exists():
                content = hook.read_text(encoding="utf-8")
                assert HOOK_MARKER not in content

    def test_skips_if_not_installed(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        result = uninstall_hooks(repo)
        assert result.installed == []
        assert set(result.skipped) == set(HOOK_NAMES)
