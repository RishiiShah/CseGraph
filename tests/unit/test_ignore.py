import pytest

from csegraph_core.ignore import IgnoreFilter


def _filter(lines):
    return IgnoreFilter.from_lines(lines)


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
