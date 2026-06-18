"""Shared utility for reading source code line ranges from files."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def read_source_lines(
    repo_root: str,
    file_path: str,
    start_line: int,
    end_line: int,
) -> Optional[str]:
    """Read lines [start_line, end_line] (1-based, inclusive) from a file.

    Returns None if the file doesn't exist or can't be read.
    Raises ValueError if the resolved path escapes repo_root.
    """
    root = Path(repo_root).resolve()
    resolved = (root / file_path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path escapes repo root: {file_path!r} resolves to {resolved}")
    if not resolved.is_file():
        return None
    try:
        with open(resolved, encoding="utf-8", newline="") as fh:
            lines = fh.readlines()
    except OSError:
        return None
    start = max(0, start_line - 1)
    end = min(len(lines), end_line)
    return "".join(lines[start:end])
