from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from csegraph_core.languages.base import Parser, Tokenizer


class UnsupportedLanguageError(KeyError):
    pass


class LanguageRegistry:
    def __init__(self) -> None:
        self._parsers: List[Parser] = []
        self._tokenizers: Dict[str, Tokenizer] = {}

    def register(self, parser: Parser, tokenizer: Tokenizer) -> None:
        self._parsers.append(parser)
        self._tokenizers[parser.language] = tokenizer

    def for_extension(self, ext: str) -> Parser:
        for parser in self._parsers:
            if ext in parser.extensions:
                return parser
        raise UnsupportedLanguageError(f"No parser registered for extension {ext!r}")

    def iter_files(self, root: Path) -> Iterable[Tuple[Parser, Path]]:
        for parser in self._parsers:
            for path in parser.iter_files(root):
                yield parser, path

    def tokenizer_for(self, language: str) -> Tokenizer:
        try:
            return self._tokenizers[language]
        except KeyError:
            raise UnsupportedLanguageError(f"No tokenizer registered for language {language!r}")


registry = LanguageRegistry()
