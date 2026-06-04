import subprocess

import pytest

from csegraph_core.ignore import IgnoreFilter, load_ignore_filter


def _filter(lines):
    return IgnoreFilter.from_lines(lines)


def _git(repo, *args):
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


class TestBlankAndComments:
    def test_blank_lines_ignored(self):
        f = _filter(["", "  ", "*.py"])
        assert f.is_ignored("foo.py")

    def test_comments_ignored(self):
        f = _filter(["# this is a comment", "*.py"])
        assert f.is_ignored("foo.py")


class TestGlobPatterns:
    def test_star_extension(self):
        f = _filter(["*.generated.py"])
        assert f.is_ignored("models.generated.py")
        assert f.is_ignored("sub/models.generated.py")
        assert not f.is_ignored("models.py")

    def test_star_matches_basename(self):
        f = _filter(["*.txt"])
        assert f.is_ignored("readme.txt")
        assert f.is_ignored("docs/readme.txt")
        assert not f.is_ignored("readme.py")

    def test_exact_name(self):
        f = _filter(["setup.py"])
        assert f.is_ignored("setup.py")
        assert f.is_ignored("sub/setup.py")
        assert not f.is_ignored("my_setup.py")


class TestDirectoryPatterns:
    def test_trailing_slash_only_matches_dirs(self):
        f = _filter(["data/"])
        assert f.is_ignored("data", is_dir=True)
        assert not f.is_ignored("data")  # file named data
        assert f.is_ignored("sub/data", is_dir=True)

    def test_dir_name_only(self):
        f = _filter(["__pycache__/"])
        assert f.is_ignored("__pycache__", is_dir=True)
        assert f.is_ignored("pkg/__pycache__", is_dir=True)


class TestRootedPatterns:
    def test_leading_slash_anchors_to_root(self):
        f = _filter(["/data/"])
        assert f.is_ignored("data", is_dir=True)
        assert not f.is_ignored("sub/data", is_dir=True)

    def test_leading_slash_file(self):
        f = _filter(["/config.py"])
        assert f.is_ignored("config.py")
        assert not f.is_ignored("sub/config.py")

    def test_slash_in_pattern_is_anchored(self):
        f = _filter(["docs/*.txt"])
        assert f.is_ignored("docs/readme.txt")
        assert not f.is_ignored("sub/docs/readme.txt")


class TestNegation:
    def test_negation_overrides(self):
        f = _filter(["*.py", "!important.py"])
        assert f.is_ignored("foo.py")
        assert not f.is_ignored("important.py")
        assert not f.is_ignored("sub/important.py")

    def test_negation_only_applies_after_match(self):
        f = _filter(["!*.py"])
        assert not f.is_ignored("foo.py")
        assert not f.is_ignored("foo.txt")

    def test_negation_then_re_ignore(self):
        f = _filter(["*.log", "!important.log", "really_*.log"])
        assert f.is_ignored("app.log")
        assert not f.is_ignored("important.log")
        assert f.is_ignored("really_important.log")


class TestEmptyFilter:
    def test_empty_filter_ignores_nothing(self):
        f = _filter([])
        assert not f.is_ignored("anything.py")
        assert not f.is_ignored("dir", is_dir=True)

    def test_no_file_produces_empty_filter(self, tmp_path):
        f = IgnoreFilter.from_file(tmp_path / ".csegraphignore")
        assert not f.is_ignored("anything.py")


class TestFromFile:
    def test_reads_file(self, tmp_path):
        ignore_file = tmp_path / ".csegraphignore"
        ignore_file.write_text("*.log\ndata/\n", encoding="utf-8")
        f = IgnoreFilter.from_file(ignore_file)
        assert f.is_ignored("app.log")
        assert f.is_ignored("data", is_dir=True)
        assert not f.is_ignored("app.py")


class TestGitAwareFilter:
    def test_gitignore_excludes_untracked_files(self, tmp_path):
        _git(tmp_path, "init")
        (tmp_path / ".gitignore").write_text("generated.py\ndata/\n", encoding="utf-8")
        (tmp_path / "app.py").write_text("def app():\n    pass\n", encoding="utf-8")
        (tmp_path / "generated.py").write_text("def generated():\n    pass\n", encoding="utf-8")
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "seed.py").write_text("def seed():\n    pass\n", encoding="utf-8")
        _git(tmp_path, "add", ".gitignore", "app.py")

        f = load_ignore_filter(tmp_path)

        assert not f.is_ignored("app.py")
        assert f.is_ignored("generated.py")
        assert f.is_ignored("data", is_dir=True)
        assert f.is_ignored("data/seed.py")

    def test_tracked_gitignored_file_is_not_ignored_by_gitignore(self, tmp_path):
        _git(tmp_path, "init")
        (tmp_path / ".gitignore").write_text("*.py\n", encoding="utf-8")
        (tmp_path / "tracked.py").write_text("def tracked():\n    pass\n", encoding="utf-8")
        (tmp_path / "untracked.py").write_text("def untracked():\n    pass\n", encoding="utf-8")
        _git(tmp_path, "add", ".gitignore")
        _git(tmp_path, "add", "-f", "tracked.py")

        f = load_ignore_filter(tmp_path)

        assert not f.is_ignored("tracked.py")
        assert f.is_ignored("untracked.py")

    def test_csegraphignore_overrides_gitignore_for_untracked_file(self, tmp_path):
        _git(tmp_path, "init")
        (tmp_path / ".gitignore").write_text("*.py\n", encoding="utf-8")
        (tmp_path / ".csegraphignore").write_text("!keep.py\n", encoding="utf-8")
        (tmp_path / "keep.py").write_text("def keep():\n    pass\n", encoding="utf-8")
        (tmp_path / "drop.py").write_text("def drop():\n    pass\n", encoding="utf-8")
        _git(tmp_path, "add", ".gitignore", ".csegraphignore")

        f = load_ignore_filter(tmp_path)

        assert not f.is_ignored("keep.py")
        assert f.is_ignored("drop.py")

    def test_csegraphignore_excludes_tracked_file(self, tmp_path):
        _git(tmp_path, "init")
        (tmp_path / ".gitignore").write_text("*.py\n", encoding="utf-8")
        (tmp_path / ".csegraphignore").write_text("tracked.py\n", encoding="utf-8")
        (tmp_path / "tracked.py").write_text("def tracked():\n    pass\n", encoding="utf-8")
        _git(tmp_path, "add", ".gitignore", ".csegraphignore")
        _git(tmp_path, "add", "-f", "tracked.py")

        f = load_ignore_filter(tmp_path)

        assert f.is_ignored("tracked.py")

    def test_should_descend_into_gitignored_dir_with_tracked_descendant(self, tmp_path):
        _git(tmp_path, "init")
        (tmp_path / ".gitignore").write_text("data/\n", encoding="utf-8")
        data = tmp_path / "data"
        data.mkdir()
        (data / "tracked.py").write_text("def tracked():\n    pass\n", encoding="utf-8")
        (data / "untracked.py").write_text("def untracked():\n    pass\n", encoding="utf-8")
        _git(tmp_path, "add", ".gitignore")
        _git(tmp_path, "add", "-f", "data/tracked.py")

        f = load_ignore_filter(tmp_path)

        assert f.should_descend("data")
        assert not f.is_ignored("data/tracked.py")
        assert f.is_ignored("data/untracked.py")

    def test_csegraphignored_dir_blocks_tracked_descendant_without_negation(self, tmp_path):
        _git(tmp_path, "init")
        (tmp_path / ".csegraphignore").write_text("data/\n", encoding="utf-8")
        data = tmp_path / "data"
        data.mkdir()
        (data / "tracked.py").write_text("def tracked():\n    pass\n", encoding="utf-8")
        _git(tmp_path, "add", ".csegraphignore", "data/tracked.py")

        f = load_ignore_filter(tmp_path)

        assert not f.should_descend("data")
        assert f.is_ignored("data/tracked.py")
