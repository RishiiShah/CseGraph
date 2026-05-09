from __future__ import annotations


def test_parsed_file_same_class_from_both_locations():
    from csegraph_core.languages.types import ParsedFile as A
    from csegraph_core.languages.python.parser import ParsedFile as B
    assert A is B


def test_parsed_symbol_same_class_from_both_locations():
    from csegraph_core.languages.types import ParsedSymbol as A
    from csegraph_core.languages.python.parser import ParsedSymbol as B
    assert A is B


def test_parsed_file_can_be_instantiated():
    from csegraph_core.languages.types import ParsedFile
    pf = ParsedFile(
        rel_path="pkg/util.py",
        abs_path="/repo/pkg/util.py",
        sha256="abc",
        mtime=0.0,
        size=100,
    )
    assert pf.language == "python"
    assert pf.symbols == []
