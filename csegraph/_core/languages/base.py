"""BaseParser ABC, shared utilities, and Tokenizer protocol."""

from __future__ import annotations

import abc
import hashlib
from pathlib import Path, PurePosixPath
from typing import Dict, FrozenSet, List, Optional, Protocol, runtime_checkable

from csegraph._core.languages.types import ParsedFile
from csegraph._core.text.tokens import _default_text_tokenize

EXCLUDED_DIRS: FrozenSet[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        "node_modules",
        "venv",
        ".venv",
        "env",
        "site-packages",
    }
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def to_repo_relative(path: Path, root_dir: Path) -> str:
    resolved_path = path.resolve()
    resolved_root = root_dir.resolve()
    try:
        return resolved_path.relative_to(resolved_root).as_posix()
    except ValueError as exc:
        raise ValueError(
            f"Path '{path}' resolves to '{resolved_path}', which is outside repository root '{root_dir}'"
        ) from exc


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

    def excludes_rel_path(self, rel_path: str) -> bool:
        extra_excluded = set(self.extra_excluded_dirs)
        if not extra_excluded:
            extra_excluded = set(self.excluded_dirs) - set(EXCLUDED_DIRS)
        if not extra_excluded:
            return False
        dirs = PurePosixPath(rel_path).parts[:-1]
        return any(part in extra_excluded for part in dirs)

    def iter_files(self, root_dir: Path) -> List[Path]:
        from csegraph._core.discovery import iter_discoverable_rel_paths
        from csegraph._core.ignore import load_ignore_filter

        ignore = load_ignore_filter(root_dir)
        resolved_root = root_dir.resolve()
        paths: List[Path] = []
        for rel_path in iter_discoverable_rel_paths(resolved_root, ignore=ignore):
            if self.excludes_rel_path(rel_path):
                continue
            if not any(rel_path.endswith(ext) for ext in self.extensions):
                continue
            path = resolved_root / rel_path
            if path.is_file() and not path.is_symlink():
                paths.append(path)
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
