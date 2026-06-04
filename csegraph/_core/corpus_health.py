"""Index / corpus health checks for code repositories.

Surfaces whether the SQLite index is substantial enough to trust, stale relative
to git or node timestamps, or expensive to query — without running a full status
command.
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Dict, List, Optional, Sequence

from csegraph._core.core.models import IndexHealth

# Code-oriented thresholds (file/symbol/LOC counts, not document word counts).
_THIN_FILES = int(os.environ.get("CSEGRAPH_HEALTH_THIN_FILES", "3"))
_THIN_SYMBOLS = int(os.environ.get("CSEGRAPH_HEALTH_THIN_SYMBOLS", "10"))
_THIN_LOC = int(os.environ.get("CSEGRAPH_HEALTH_THIN_LOC", "200"))
_LARGE_FILES = int(os.environ.get("CSEGRAPH_HEALTH_LARGE_FILES", "500"))
_LARGE_SYMBOLS = int(os.environ.get("CSEGRAPH_HEALTH_LARGE_SYMBOLS", "8000"))
_STALE_HOURS = float(os.environ.get("CSEGRAPH_HEALTH_STALE_HOURS", "24"))
_PARSE_ERROR_RATIO = float(os.environ.get("CSEGRAPH_HEALTH_PARSE_ERROR_RATIO", "0.15"))


def collect_index_metrics(conn: sqlite3.Connection) -> Dict[str, int]:
    """Aggregate counts used for health assessment."""
    file_count = int(
        conn.execute("SELECT COUNT(*) FROM nodes WHERE type = 'file'").fetchone()[0]
    )
    symbol_count = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM nodes
            WHERE type IN ('class', 'function', 'method', 'test')
            """
        ).fetchone()[0]
    )
    edge_count = int(conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
    parse_error_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE type = 'file' AND parse_status = 'error'"
        ).fetchone()[0]
    )
    approx_loc = 0
    loc_row = conn.execute(
        """
        SELECT COALESCE(SUM(MAX(0, end_line - start_line + 1)), 0)
        FROM nodes
        WHERE type IN ('class', 'function', 'method', 'test')
          AND start_line IS NOT NULL AND end_line IS NOT NULL
        """
    ).fetchone()
    if loc_row:
        approx_loc = int(loc_row[0] or 0)

    fts_entries = 0
    if _table_exists(conn, "lexical_index"):
        fts_entries = int(conn.execute("SELECT COUNT(*) FROM lexical_index").fetchone()[0])

    return {
        "files": file_count,
        "symbols": symbol_count,
        "edges": edge_count,
        "parse_errors": parse_error_count,
        "approx_loc": approx_loc,
        "fts_entries": fts_entries,
    }


def index_age_hours(
    *,
    metadata_updated_at: Optional[str],
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[float]:
    """Hours since last index activity (metadata or newest node)."""
    candidates: List[float] = []
    if metadata_updated_at:
        try:
            candidates.append(float(metadata_updated_at))
        except ValueError:
            pass
    if conn is not None:
        try:
            row = conn.execute("SELECT MAX(updated_at) FROM nodes").fetchone()
            if row and row[0] is not None:
                candidates.append(float(row[0]))
        except Exception:
            pass
    if not candidates:
        return None
    return max(0.0, (time.time() - max(candidates)) / 3600.0)


def assess_index_health(
    metrics: Dict[str, int],
    *,
    index_age_hours: Optional[float] = None,
    external_warnings: Optional[Sequence[str]] = None,
) -> IndexHealth:
    """Return verdict, one-line summary, and agent-facing hints."""
    files = int(metrics.get("files", 0))
    symbols = int(metrics.get("symbols", 0))
    edges = int(metrics.get("edges", 0))
    parse_errors = int(metrics.get("parse_errors", 0))
    approx_loc = int(metrics.get("approx_loc", 0))
    fts_entries = int(metrics.get("fts_entries", 0))

    hints: List[str] = []
    verdict = "ok"
    summary = (
        f"Index looks usable: {files} files, {symbols} symbols, {edges} edges"
        f" (~{approx_loc:,} LOC indexed)."
    )

    ext = list(external_warnings or [])
    for msg in ext:
        if "schema mismatch" in msg.lower() or "rebuild" in msg.lower():
            verdict = "rebuild"
            summary = msg
            hints.append("Run `csegraph index` to rebuild the index.")
            break

    if verdict == "rebuild":
        return IndexHealth(
            verdict=verdict,
            summary=summary,
            index_age_hours=index_age_hours,
            metrics=_metrics_payload(metrics),
            hints=hints,
        )

    for msg in ext:
        if "commit" in msg.lower() and "head" in msg.lower():
            if verdict == "ok":
                verdict = "stale"
            hints.append("Run `csegraph refresh` to sync with HEAD.")
        elif "branch" in msg.lower() and "built on" in msg.lower():
            if verdict == "ok":
                verdict = "stale"
            hints.append("Run `csegraph index` after switching branches.")

    if index_age_hours is not None and index_age_hours >= _STALE_HOURS:
        if verdict not in ("rebuild",):
            verdict = "stale"
        hours = int(index_age_hours)
        hints.append(
            f"Index last updated ~{hours}h ago; run `csegraph refresh` before trusting context."
        )

    if files == 0:
        verdict = "thin"
        summary = "Index has no files; run `csegraph index` on this repository."
        hints.append("Run `csegraph index .` before calling context tools.")
    elif (
        files < _THIN_FILES
        or symbols < _THIN_SYMBOLS
        or (approx_loc > 0 and approx_loc < _THIN_LOC)
    ):
        if verdict == "ok":
            verdict = "thin"
            summary = (
                f"Small index ({files} files, {symbols} symbols, ~{approx_loc:,} LOC). "
                "A graph may add little over reading files directly."
            )
            hints.append(
                "For tiny repos, prefer targeted file reads; use `csegraph_context` only when you need blast radius."
            )
    elif files >= _LARGE_FILES or symbols >= _LARGE_SYMBOLS:
        if verdict == "ok":
            verdict = "large"
        summary = (
            f"Large index ({files} files, {symbols} symbols). "
            "Use `detail_level=auto` and a specific target to limit tokens."
        )
        hints.append("Pass `target` on `csegraph_context`; avoid `detail_level=full` without a narrow symbol.")

    if parse_errors > 0 and files > 0:
        ratio = parse_errors / files
        if ratio >= _PARSE_ERROR_RATIO:
            if verdict == "ok":
                verdict = "errors"
            hints.append(
                f"{parse_errors} file(s) failed parsing; context may miss those paths. "
                "Run `csegraph status --verbose` for paths."
            )

    if fts_entries == 0 and files >= _THIN_FILES:
        hints.append(
            "Lexical search (FTS) is empty; run `csegraph postprocess . --level minimal` or re-index with postprocess."
        )

    if verdict == "ok" and hints:
        summary = summary + " " + hints[0]
    elif verdict == "stale" and index_age_hours is not None:
        summary = (
            f"Index may be stale (~{int(index_age_hours)}h old, {files} files, {symbols} symbols). "
            "Refresh before review or debug tasks."
        )
    elif verdict == "thin":
        pass
    elif verdict == "large":
        pass
    elif verdict == "errors":
        summary = (
            f"Index has parse errors on {parse_errors} of {files} files; "
            "verify language support or fix syntax."
        )

    # Dedupe hints while preserving order
    seen: set[str] = set()
    unique_hints: List[str] = []
    for hint in hints:
        if hint not in seen:
            seen.add(hint)
            unique_hints.append(hint)

    return IndexHealth(
        verdict=verdict,
        summary=summary,
        index_age_hours=index_age_hours,
        metrics=_metrics_payload(metrics),
        hints=unique_hints,
    )


def _metrics_payload(metrics: Dict[str, int]) -> Dict[str, int]:
    return {
        "files": int(metrics.get("files", 0)),
        "symbols": int(metrics.get("symbols", 0)),
        "edges": int(metrics.get("edges", 0)),
        "parse_errors": int(metrics.get("parse_errors", 0)),
        "approx_loc": int(metrics.get("approx_loc", 0)),
        "fts_entries": int(metrics.get("fts_entries", 0)),
    }


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None
