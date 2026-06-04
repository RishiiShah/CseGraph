from __future__ import annotations

from typing import Any, Dict


def line_count(row: Dict[str, Any]) -> int:
    start = row.get("start_line")
    end = row.get("end_line")
    if start is None or end is None:
        return 0
    return max(0, int(end) - int(start) + 1)


def is_small_helper_row(row: Dict[str, Any], max_lines: int = 12) -> bool:
    return (
        row.get("kind") in {"function", "method"}
        and line_count(row) <= max_lines
    )
