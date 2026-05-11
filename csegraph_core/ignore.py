"""Ignore-file handling for .csegraphignore.

Supports a practical .gitignore-like subset:
  - blank lines and ``#`` comments
  - glob patterns (``*.generated.py``)
  - directory patterns (``data/``)
  - rooted patterns (``/data/``, ``/scripts/*.py``)
  - negation (``!important.py``)

Patterns without a ``/`` (other than a leading or trailing one) match
against the basename only.  Patterns containing ``/`` match against
the full repo-relative path.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import List, Optional, Tuple

IGNORE_FILENAME = ".csegraphignore"

# (negate, pattern, dir_only, anchored)
_Rule = Tuple[bool, str, bool, bool]


def load_ignore_filter(root: Path) -> "IgnoreFilter":
    return IgnoreFilter.from_file(root / IGNORE_FILENAME)


def _parse_line(line: str) -> Optional[_Rule]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    negate = False
    if stripped.startswith("!"):
        negate = True
        stripped = stripped[1:]
        if not stripped:
            return None

    dir_only = stripped.endswith("/")
    if dir_only:
        stripped = stripped.rstrip("/")

    anchored = stripped.startswith("/")
    if anchored:
        stripped = stripped.lstrip("/")
    elif "/" in stripped:
        anchored = True

    return (negate, stripped, dir_only, anchored)


class IgnoreFilter:
    __slots__ = ("_rules",)

    def __init__(self, rules: List[_Rule]) -> None:
        self._rules = rules

    @classmethod
    def from_lines(cls, lines: List[str]) -> "IgnoreFilter":
        rules: List[_Rule] = []
        for line in lines:
            parsed = _parse_line(line)
            if parsed is not None:
                rules.append(parsed)
        return cls(rules)

    @classmethod
    def from_file(cls, path: Path) -> "IgnoreFilter":
        if not path.is_file():
            return cls([])
        text = path.read_text(encoding="utf-8")
        return cls.from_lines(text.splitlines())

    def is_ignored(self, rel_path: str, *, is_dir: bool = False) -> bool:
        if not self._rules:
            return False
        result = False
        for negate, pattern, dir_only, anchored in self._rules:
            if dir_only and not is_dir:
                continue
            if _matches(rel_path, pattern, anchored):
                result = not negate
        return result


def _matches(rel_path: str, pattern: str, anchored: bool) -> bool:
    if anchored:
        return fnmatch.fnmatch(rel_path, pattern)
    basename = rel_path.rsplit("/", 1)[-1] if "/" in rel_path else rel_path
    return fnmatch.fnmatch(basename, pattern)
