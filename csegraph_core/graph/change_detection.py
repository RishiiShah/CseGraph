"""Change detection: maps git diffs to graph nodes and scores risk."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set, Tuple

from csegraph_core.index.repository import ProjectIndex


@dataclass
class ChangedSymbol:
    id: str
    name: str
    kind: str
    path: str
    line_range: Optional[List[int]]
    risk_score: float
    risk_level: str
    caller_count: int
    cross_community_edges: int
    has_test_coverage: bool
    community_id: Optional[int]
    risk_factors: List[str] = field(default_factory=list)


@dataclass
class ChangeDetectionResult:
    command: str
    db_path: str
    repo_root: str
    base_ref: str
    changed_files: List[str]
    total_changed_symbols: int
    high_risk: List[ChangedSymbol]
    medium_risk: List[ChangedSymbol]
    low_risk: List[ChangedSymbol]
    summary: str
    communities_affected: int
    warnings: List[str] = field(default_factory=list)


@dataclass
class DiffRegion:
    path: str
    changed_lines: List[Tuple[int, int]]
    is_new_file: bool = False
    is_deleted_file: bool = False


_DIFF_HEADER_RE = re.compile(r"^diff --git a/.+ b/(.+)$")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_NEW_FILE_RE = re.compile(r"^new file mode")
_DELETED_FILE_RE = re.compile(r"^deleted file mode")


def _parse_diff(diff_text: str) -> List[DiffRegion]:
    regions: List[DiffRegion] = []
    current_path: Optional[str] = None
    current_lines: List[Tuple[int, int]] = []
    is_new = False
    is_deleted = False

    def _flush() -> None:
        nonlocal current_path, current_lines, is_new, is_deleted
        if current_path is not None:
            regions.append(DiffRegion(
                path=current_path,
                changed_lines=current_lines,
                is_new_file=is_new,
                is_deleted_file=is_deleted,
            ))
        current_path = None
        current_lines = []
        is_new = False
        is_deleted = False

    for line in diff_text.splitlines():
        m = _DIFF_HEADER_RE.match(line)
        if m:
            _flush()
            current_path = m.group(1)
            continue
        if current_path is None:
            continue
        if _NEW_FILE_RE.match(line):
            is_new = True
            continue
        if _DELETED_FILE_RE.match(line):
            is_deleted = True
            continue
        m = _HUNK_RE.match(line)
        if m:
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) else 1
            if count > 0:
                current_lines.append((start, start + count - 1))

    _flush()
    return regions


def _lines_overlap(
    sym_start: int,
    sym_end: int,
    regions: List[Tuple[int, int]],
) -> bool:
    for start, end in regions:
        if sym_start <= end and start <= sym_end:
            return True
    return False


def _compute_risk(
    caller_count: int,
    cross_community_edges: int,
    has_test_coverage: bool,
) -> Tuple[float, str, List[str]]:
    factors: List[str] = []

    caller_score = min(caller_count / 10.0, 1.0)
    if caller_count > 0:
        factors.append(f"{caller_count} caller(s)")

    cc_score = min(cross_community_edges / 5.0, 1.0)
    if cross_community_edges > 0:
        factors.append(f"{cross_community_edges} cross-community edge(s)")

    test_penalty = 0.0 if has_test_coverage else 1.0
    if not has_test_coverage:
        factors.append("no test coverage")

    risk_score = round(0.5 * caller_score + 0.2 * cc_score + 0.3 * test_penalty, 3)

    if risk_score >= 0.6:
        level = "high"
    elif risk_score >= 0.3:
        level = "medium"
    else:
        level = "low"

    return risk_score, level, factors


class ChangeDetectionService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def detect_changes(self, base_ref: str = "HEAD~1") -> ChangeDetectionResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]
            diff_text = _git_diff(repo_root, base_ref)
            regions = _parse_diff(diff_text)
            return self._analyze(index, repo_root, base_ref, regions)
        finally:
            index.close()

    def analyze_regions(
        self,
        regions: List[DiffRegion],
        base_ref: str = "",
    ) -> ChangeDetectionResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]
            return self._analyze(index, repo_root, base_ref, regions)
        finally:
            index.close()

    def _analyze(
        self,
        index: ProjectIndex,
        repo_root: str,
        base_ref: str,
        regions: List[DiffRegion],
    ) -> ChangeDetectionResult:
        warnings: List[str] = []
        changed_files: List[str] = []
        affected_ids: Set[str] = set()

        for region in regions:
            if region.is_deleted_file:
                continue
            changed_files.append(region.path)

            if region.is_new_file:
                rows = index.conn.execute(
                    """SELECT id FROM nodes
                       WHERE path = ? AND type IN ('class','function','method','test')""",
                    (region.path,),
                ).fetchall()
            else:
                if not region.changed_lines:
                    continue
                rows = index.conn.execute(
                    """SELECT id, start_line, end_line FROM nodes
                       WHERE path = ? AND type IN ('class','function','method','test')
                         AND start_line IS NOT NULL AND end_line IS NOT NULL""",
                    (region.path,),
                ).fetchall()
                rows = [
                    r for r in rows
                    if _lines_overlap(r["start_line"], r["end_line"], region.changed_lines)
                ]

            if not rows:
                file_exists = index.conn.execute(
                    "SELECT 1 FROM nodes WHERE path = ? AND type = 'file'",
                    (region.path,),
                ).fetchone()
                if file_exists is None and not region.is_new_file:
                    warnings.append(f"'{region.path}' not in index")
                continue

            for row in rows:
                affected_ids.add(row["id"])

        changed_symbols: List[ChangedSymbol] = []
        communities_seen: Set[int] = set()

        for sym_id in sorted(affected_ids):
            row = index.conn.execute(
                """SELECT id, name, type, path, start_line, end_line,
                          community_id, is_test
                   FROM nodes WHERE id = ?""",
                (sym_id,),
            ).fetchone()
            if row is None:
                continue

            caller_count = index.conn.execute(
                "SELECT COUNT(*) AS cnt FROM edges WHERE target = ? AND relation IN ('calls','inherits')",
                (sym_id,),
            ).fetchone()["cnt"]

            community_id = row["community_id"]
            cross_community = 0
            if community_id is not None:
                communities_seen.add(community_id)
                cross_community = index.conn.execute(
                    """SELECT COUNT(*) AS cnt FROM (
                        SELECT e.id FROM edges e
                        JOIN nodes n ON n.id = e.target
                        WHERE e.source = ? AND n.community_id IS NOT NULL AND n.community_id != ?
                        UNION ALL
                        SELECT e.id FROM edges e
                        JOIN nodes n ON n.id = e.source
                        WHERE e.target = ? AND n.community_id IS NOT NULL AND n.community_id != ?
                    )""",
                    (sym_id, community_id, sym_id, community_id),
                ).fetchone()["cnt"]

            has_test = bool(row["is_test"])
            if not has_test:
                has_test = index.conn.execute(
                    "SELECT COUNT(*) AS cnt FROM edges WHERE (source = ? OR target = ?) AND relation = 'tested_by'",
                    (sym_id, sym_id),
                ).fetchone()["cnt"] > 0

            risk_score, risk_level, risk_factors = _compute_risk(
                caller_count, cross_community, has_test,
            )

            start = row["start_line"]
            end = row["end_line"]
            line_range = [start, end] if start is not None and end is not None else None

            changed_symbols.append(ChangedSymbol(
                id=sym_id,
                name=row["name"],
                kind=row["type"],
                path=row["path"],
                line_range=line_range,
                risk_score=risk_score,
                risk_level=risk_level,
                caller_count=caller_count,
                cross_community_edges=cross_community,
                has_test_coverage=has_test,
                community_id=community_id,
                risk_factors=risk_factors,
            ))

        changed_symbols.sort(key=lambda s: (-s.risk_score, s.name))

        high = [s for s in changed_symbols if s.risk_level == "high"]
        medium = [s for s in changed_symbols if s.risk_level == "medium"]
        low = [s for s in changed_symbols if s.risk_level == "low"]

        total = len(changed_symbols)
        parts = [f"{total} changed symbol(s) across {len(changed_files)} file(s)"]
        if high:
            parts.append(f"{len(high)} high-risk")
        if medium:
            parts.append(f"{len(medium)} medium-risk")
        if low:
            parts.append(f"{len(low)} low-risk")

        return ChangeDetectionResult(
            command="detect-changes",
            db_path=self.db_path,
            repo_root=repo_root,
            base_ref=base_ref,
            changed_files=changed_files,
            total_changed_symbols=total,
            high_risk=high,
            medium_risk=medium,
            low_risk=low,
            summary=". ".join(parts) + ".",
            communities_affected=len(communities_seen),
            warnings=warnings,
        )


def _git_diff(repo_root: str, base_ref: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", repo_root, "diff", "--unified=0", base_ref],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git diff failed (exit {result.returncode}): {result.stderr.strip()}"
            )
        return result.stdout
    except FileNotFoundError:
        raise RuntimeError("git is not available on the system PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError("git diff timed out after 30 seconds")
