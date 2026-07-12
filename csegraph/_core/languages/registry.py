from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from csegraph._core.discovery import iter_discoverable_rel_paths
from csegraph._core.ignore import load_ignore_filter
from csegraph._core.languages.base import Parser, Tokenizer


class UnsupportedLanguageError(KeyError):
    pass


class LanguageRegistry:
    def __init__(self) -> None:
        self._parsers: List[Parser] = []
        self._tokenizers: Dict[str, Tokenizer] = {}
        self._ext_to_parser: Dict[str, Parser] = {}
        self._explicit_only_extensions: Set[str] = set()

    def register(self, parser: Parser, tokenizer: Tokenizer) -> None:
        self._parsers.append(parser)
        self._tokenizers[parser.language] = tokenizer
        for ext in parser.extensions:
            if ext not in self._ext_to_parser:
                self._ext_to_parser[ext] = parser

    def register_explicit(self, parser: Parser, tokenizer: Tokenizer) -> None:
        self.register(parser, tokenizer)
        self._explicit_only_extensions.update(parser.extensions)

    def for_extension(self, ext: str) -> Parser:
        try:
            return self._ext_to_parser[ext]
        except KeyError as exc:
            raise UnsupportedLanguageError(f"No parser registered for extension {ext!r}") from exc

    def iter_files(
        self,
        root: Path,
        *,
        exclude_patterns: Optional[Sequence[str]] = None,
    ) -> Iterable[Tuple[Parser, Path]]:
        ignore = load_ignore_filter(root, exclude_patterns=exclude_patterns)
        resolved_root = root.resolve()
        results: List[Tuple[Parser, Path]] = []

        for rel_path in iter_discoverable_rel_paths(resolved_root, ignore=ignore):
            ext = os.path.splitext(rel_path)[1]
            parser = self._ext_to_parser.get(ext)
            if parser is None:
                continue
            if ext in self._explicit_only_extensions and not ignore.is_explicitly_included(
                rel_path
            ):
                continue
            if parser.excludes_rel_path(rel_path):
                continue
            path = resolved_root / rel_path
            if path.is_file() and not path.is_symlink():
                results.append((parser, path))

        return results

    def supported_extensions(self) -> Set[str]:
        return set(self._ext_to_parser.keys())

    def tokenizer_for(self, language: str) -> Tokenizer:
        try:
            return self._tokenizers[language]
        except KeyError as exc:
            raise UnsupportedLanguageError(
                f"No tokenizer registered for language {language!r}"
            ) from exc


registry = LanguageRegistry()


def _register_builtin_languages() -> None:
    from csegraph._core.languages.base import DefaultTokenizer
    from csegraph._core.languages.treesitter.languages import LANGUAGE_FACTORIES
    from csegraph._core.languages.treesitter.parser import TreeSitterParser

    for factory in LANGUAGE_FACTORIES:
        registry.register(TreeSitterParser(factory()), DefaultTokenizer())


_register_builtin_languages()


def __getattr__(name: str):
    return getattr(registry, name)
