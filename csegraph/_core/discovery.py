"""File discovery for indexing.

Discovery prefers ``git ls-files`` (staged and committed, with submodules by
default), then ``svn list -R`` for SVN working copies, then a bounded directory
walk. Untracked files in git repos are skipped until ``git add``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional

from csegraph._core.ignore import IgnoreFilter, load_ignore_filter
from csegraph._core.languages.base import EXCLUDED_DIRS


def iter_discoverable_rel_paths(
    root: Path,
    *,
    ignore: Optional[IgnoreFilter] = None,
) -> Iterable[str]:
    """Yield repo-relative paths that may be parsed and indexed."""
    root = root.resolve()
    if ignore is None:
        ignore = load_ignore_filter(root)
    if ignore.vcs and ignore.index_paths:
        for rel in sorted(ignore.index_paths):
            if not ignore.is_ignored(rel):
                yield rel
        return
    yield from _walk_rel_paths(root, ignore)


def is_discoverable_rel_path(rel_path: str, ignore: IgnoreFilter) -> bool:
    rel_path = rel_path.replace("\\", "/").strip("/")
    if not rel_path:
        return False
    if ignore.vcs and ignore.index_paths and rel_path not in ignore.index_paths:
        return False
    return not ignore.is_ignored(rel_path)


def _walk_rel_paths(root: Path, ignore: IgnoreFilter) -> Iterable[str]:
    resolved_root = root.resolve()
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
            rel_path = f"{rel_root}/{filename}" if rel_root != "." else filename
            if not ignore.is_ignored(rel_path):
                yield rel_path
