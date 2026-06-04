from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Set, Tuple

from csegraph_core.languages.base import EXCLUDED_DIRS, Parser, Tokenizer


class UnsupportedLanguageError(KeyError):
    pass


class LanguageRegistry:
    def __init__(self) -> None:
        self._parsers: List[Parser] = []
        self._tokenizers: Dict[str, Tokenizer] = {}
        self._ext_to_parser: Dict[str, Parser] = {}

    def register(self, parser: Parser, tokenizer: Tokenizer) -> None:
        self._parsers.append(parser)
        self._tokenizers[parser.language] = tokenizer
        for ext in parser.extensions:
            if ext not in self._ext_to_parser:
                self._ext_to_parser[ext] = parser

    def for_extension(self, ext: str) -> Parser:
        try:
            return self._ext_to_parser[ext]
        except KeyError:
            raise UnsupportedLanguageError(f"No parser registered for extension {ext!r}")

    def iter_files(self, root: Path) -> Iterable[Tuple[Parser, Path]]:
        from csegraph_core.ignore import load_ignore_filter

        ignore = load_ignore_filter(root)
        resolved_root = root.resolve()
        results: List[Tuple[Parser, Path]] = []

        for dirpath, dirnames, filenames in os.walk(root):
            rel_root = Path(dirpath).resolve().relative_to(resolved_root).as_posix()
            dirnames[:] = sorted(
                name for name in dirnames
                if name not in EXCLUDED_DIRS
                and not name.startswith(".")
                and ignore.should_descend(
                    f"{rel_root}/{name}" if rel_root != "." else name,
                )
            )
            for filename in sorted(filenames):
                if filename.startswith("."):
                    continue
                ext = os.path.splitext(filename)[1]
                parser = self._ext_to_parser.get(ext)
                if parser is None:
                    continue
                rel_path = f"{rel_root}/{filename}" if rel_root != "." else filename
                if not ignore.is_ignored(rel_path) and not _parser_excludes_path(parser, rel_path):
                    results.append((parser, Path(dirpath) / filename))

        return results

    def supported_extensions(self) -> Set[str]:
        return set(self._ext_to_parser.keys())

    def tokenizer_for(self, language: str) -> Tokenizer:
        try:
            return self._tokenizers[language]
        except KeyError:
            raise UnsupportedLanguageError(f"No tokenizer registered for language {language!r}")


registry = LanguageRegistry()


def _parser_excludes_path(parser: Parser, rel_path: str) -> bool:
    extra_excluded = set(getattr(parser, "extra_excluded_dirs", frozenset()))
    if not extra_excluded:
        extra_excluded = set(getattr(parser, "excluded_dirs", frozenset())) - set(EXCLUDED_DIRS)
    if not extra_excluded:
        return False
    dirs = PurePosixPath(rel_path).parts[:-1]
    return any(part in extra_excluded for part in dirs)
