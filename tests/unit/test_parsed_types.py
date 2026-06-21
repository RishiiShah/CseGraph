from __future__ import annotations


def test_parsed_file_can_be_instantiated():
    from csegraph._core.languages.types import ParsedFile

    pf = ParsedFile(
        rel_path="pkg/util.py",
        abs_path="/repo/pkg/util.py",
        sha256="abc",
        mtime=0.0,
        size=100,
    )
    assert pf.language == "python"
    assert pf.symbols == []
