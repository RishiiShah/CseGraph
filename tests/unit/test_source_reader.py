import pytest

from csegraph._core.text.source_reader import read_source_lines


def test_reads_correct_line_range(tmp_path):
    (tmp_path / "test.py").write_bytes(b"line1\nline2\nline3\nline4\n")
    result = read_source_lines(str(tmp_path), "test.py", 2, 3)
    assert result == "line2\nline3\n"


def test_reads_single_line(tmp_path):
    (tmp_path / "test.py").write_bytes(b"only_line\n")
    result = read_source_lines(str(tmp_path), "test.py", 1, 1)
    assert result == "only_line\n"


def test_returns_none_for_missing_file(tmp_path):
    result = read_source_lines(str(tmp_path), "missing.py", 1, 1)
    assert result is None


def test_path_escape_raises_valueerror(tmp_path):
    with pytest.raises(ValueError, match="escapes repo root"):
        read_source_lines(str(tmp_path), "../../../etc/passwd", 1, 1)


def test_preserves_crlf_line_endings(tmp_path):
    (tmp_path / "win.py").write_bytes(b"line1\r\nline2\r\nline3\r\n")
    result = read_source_lines(str(tmp_path), "win.py", 1, 2)
    assert result == "line1\r\nline2\r\n"


def test_preserves_trailing_whitespace(tmp_path):
    (tmp_path / "ws.py").write_bytes(b"def foo():   \n    pass\n")
    result = read_source_lines(str(tmp_path), "ws.py", 1, 2)
    assert result == "def foo():   \n    pass\n"
