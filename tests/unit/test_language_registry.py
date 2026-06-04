from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

import pytest

from csegraph._core.languages.registry import LanguageRegistry, UnsupportedLanguageError


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


class FakePythonParser(FakeParser):
    language = "fake_python"
    extensions = (".py",)


class FakeCSharpParser(FakeParser):
    language = "fake_csharp"
    extensions = (".cs",)
    excluded_dirs = frozenset({"packages"})


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
    from csegraph._core.languages import registry
    from csegraph._core.languages.treesitter.parser import TreeSitterParser
    parser = registry.for_extension(".py")
    assert isinstance(parser, TreeSitterParser)
    assert parser.language == "python"


def test_python_tokenizer_registered_on_import():
    from csegraph._core.languages import registry
    from csegraph._core.languages.base import DefaultTokenizer
    tokenizer = registry.tokenizer_for("python")
    assert isinstance(tokenizer, DefaultTokenizer)


def test_iter_files_yields_parser_path_pairs(tmp_path):
    from csegraph._core.languages import registry
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "b.txt").write_text("not python")
    pairs = list(registry.iter_files(tmp_path))
    paths = [p for _, p in pairs]
    assert any(p.name == "a.py" for p in paths)
    assert all(p.name != "b.txt" for p in paths)


def test_parser_specific_excluded_dirs_do_not_prune_other_languages(tmp_path):
    reg = LanguageRegistry()
    python_parser = FakePythonParser()
    csharp_parser = FakeCSharpParser()
    reg.register(python_parser, FakeTokenizer())
    reg.register(csharp_parser, FakeTokenizer())

    package_dir = tmp_path / "packages" / "app"
    package_dir.mkdir(parents=True)
    (package_dir / "main.py").write_text("x = 1", encoding="utf-8")
    (package_dir / "Program.cs").write_text("class Program {}", encoding="utf-8")

    pairs = list(reg.iter_files(tmp_path))

    assert (python_parser, package_dir / "main.py") in pairs
    assert (csharp_parser, package_dir / "Program.cs") not in pairs


def test_python_parser_satisfies_widened_protocol():
    from csegraph._core.languages.base import Parser
    from csegraph._core.languages import registry
    parser = registry.for_extension(".py")
    assert isinstance(parser, Parser)
    assert hasattr(parser, "module_name_from_relpath")
    assert hasattr(parser, "resolve_local_import")


def test_typescript_parser_registered():
    from csegraph._core.languages import registry
    from csegraph._core.languages.treesitter.parser import TreeSitterParser
    parser = registry.for_extension(".ts")
    assert isinstance(parser, TreeSitterParser)
    assert parser.language == "typescript"
    assert ".tsx" in parser.extensions
    tokenizer = registry.tokenizer_for("typescript")
    assert tokenizer is not None


def test_all_supported_extensions_registered_on_import():
    from csegraph._core.languages import registry
    from csegraph._core.languages.treesitter.languages import LANGUAGE_SPECS

    expected = {
        extension
        for spec in LANGUAGE_SPECS
        for extension in spec.extensions
    }
    assert expected <= registry.supported_extensions()
