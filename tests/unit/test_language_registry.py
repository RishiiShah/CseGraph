from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

import pytest

from csegraph_core.languages.registry import LanguageRegistry, UnsupportedLanguageError


class FakeTokenizer:
    def tokenize(self, text: str) -> List[str]:
        return text.split()


class FakeParser:
    language = "fake"
    extensions = (".fake",)
    excluded_dirs = frozenset()

    def parse(self, path: Path, root: Path) -> object:
        raise NotImplementedError

    def iter_files(self, root: Path) -> Iterable[Path]:
        return []


def test_register_and_dispatch_by_extension():
    reg = LanguageRegistry()
    parser = FakeParser()
    tokenizer = FakeTokenizer()
    reg.register(parser, tokenizer)
    result = reg.for_extension(".fake")
    assert result is parser


def test_for_extension_unknown_raises():
    reg = LanguageRegistry()
    with pytest.raises(UnsupportedLanguageError):
        reg.for_extension(".unknown")


def test_tokenizer_for_unknown_raises():
    reg = LanguageRegistry()
    with pytest.raises(UnsupportedLanguageError):
        reg.tokenizer_for("unknown")


def test_python_parser_registered_on_import():
    from csegraph_core.languages import registry
    from csegraph_core.languages.treesitter.parser import TreeSitterParser
    parser = registry.for_extension(".py")
    assert isinstance(parser, TreeSitterParser)
    assert parser.language == "python"


def test_python_tokenizer_registered_on_import():
    from csegraph_core.languages import registry
    from csegraph_core.languages.base import DefaultTokenizer
    tokenizer = registry.tokenizer_for("python")
    assert isinstance(tokenizer, DefaultTokenizer)


def test_iter_files_yields_parser_path_pairs(tmp_path):
    from csegraph_core.languages import registry
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "b.txt").write_text("not python")
    pairs = list(registry.iter_files(tmp_path))
    paths = [p for _, p in pairs]
    assert any(p.name == "a.py" for p in paths)
    assert all(p.name != "b.txt" for p in paths)


def test_python_parser_satisfies_widened_protocol():
    from csegraph_core.languages.base import Parser
    from csegraph_core.languages import registry
    parser = registry.for_extension(".py")
    assert isinstance(parser, Parser)
    assert hasattr(parser, "module_name_from_relpath")
    assert hasattr(parser, "resolve_local_import")


def test_typescript_parser_registered_when_available():
    ts = pytest.importorskip("tree_sitter")
    from csegraph_core.languages import registry
    from csegraph_core.languages.treesitter.parser import TreeSitterParser
    parser = registry.for_extension(".ts")
    assert isinstance(parser, TreeSitterParser)
    assert parser.language == "typescript"
    assert ".tsx" in parser.extensions
    tokenizer = registry.tokenizer_for("typescript")
    assert tokenizer is not None
