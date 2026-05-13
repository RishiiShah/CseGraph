"""BaseParser ABC, shared utilities, and Tokenizer protocol."""
from __future__ import annotations

import abc
import hashlib
import os
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Protocol, runtime_checkable

from csegraph_core.languages.types import ParsedFile
from csegraph_core.text.tokens import _default_text_tokenize


EXCLUDED_DIRS: FrozenSet[str] = frozenset({
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
    "env",
    "site-packages",
})


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def to_repo_relative(path: Path, root_dir: Path) -> str:
    return path.resolve().relative_to(root_dir.resolve()).as_posix()


class BaseParser(abc.ABC):
    """Abstract base for all language parsers.

    Subclasses must declare class-level ``language`` and ``extensions``
    and implement ``parse()``, ``module_name_from_relpath()``, and
    ``resolve_local_import()``.  ``iter_files()`` and ``excluded_dirs``
    are provided here and shared by every language.
    """

    language: str
    extensions: tuple

    @property
    def extra_excluded_dirs(self) -> FrozenSet[str]:
        """Additional dirs to skip beyond the base set. Override if needed."""
        return frozenset()

    @property
    def excluded_dirs(self) -> FrozenSet[str]:
        return EXCLUDED_DIRS | self.extra_excluded_dirs

    def iter_files(self, root_dir: Path) -> List[Path]:
        from csegraph_core.ignore import load_ignore_filter

        ignore = load_ignore_filter(root_dir)
        resolved_root = root_dir.resolve()
        excluded = self.excluded_dirs
        paths: List[Path] = []
        for root, dirs, files in os.walk(root_dir):
            rel_root = Path(root).resolve().relative_to(resolved_root).as_posix()
            dirs[:] = sorted(
                name for name in dirs
                if name not in excluded
                and not name.startswith(".")
                and not ignore.is_ignored(
                    f"{rel_root}/{name}" if rel_root != "." else name,
                    is_dir=True,
                )
            )
            for filename in sorted(files):
                if filename.startswith("."):
                    continue
                if not any(filename.endswith(ext) for ext in self.extensions):
                    continue
                rel_path = f"{rel_root}/{filename}" if rel_root != "." else filename
                if not ignore.is_ignored(rel_path):
                    paths.append(Path(root) / filename)
        return sorted(paths)

    @abc.abstractmethod
    def parse(self, path: Path, root_dir: Path) -> ParsedFile: ...

    @abc.abstractmethod
    def module_name_from_relpath(self, rel_path: str) -> Optional[str]: ...

    @abc.abstractmethod
    def resolve_local_import(
        self,
        import_name: str,
        module_to_file_id: Dict[str, str],
        current_module: Optional[str],
    ) -> Optional[str]: ...


# Alias keeps registry.py's isinstance(x, Parser) checks working without changes.
Parser = BaseParser


class DefaultTokenizer:
    """Single tokenizer for all languages — delegates to the shared text kernel."""

    def tokenize(self, text: str) -> List[str]:
        return _default_text_tokenize(text)


@runtime_checkable
class Tokenizer(Protocol):
    def tokenize(self, text: str) -> List[str]: ...
