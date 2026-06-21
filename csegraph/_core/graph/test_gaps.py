"""Test-gap analysis: finds untested symbols and ranks hotspots by risk."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from csegraph._core.index.repository import ProjectIndex


@dataclass
class UntestedSymbol:
    id: str
    name: str
    kind: str
    path: str
    line_range: Optional[List[int]]
    caller_count: int
    cross_community_edges: int
    community_id: Optional[int]
    hotspot_score: float
    risk_factors: List[str] = field(default_factory=list)


@dataclass
class CommunityCoverage:
    community_id: int
    total_symbols: int
    tested_symbols: int
    coverage_pct: float
    untested_hotspots: List[str] = field(default_factory=list)


@dataclass
class TestGapResult:
    command: str
    db_path: str
    repo_root: str
    total_symbols: int
    tested_count: int
    untested_count: int
    coverage_pct: float
    hotspots: List[UntestedSymbol]
    community_coverage: List[CommunityCoverage]
    summary: str
    warnings: List[str] = field(default_factory=list)


def _compute_hotspot(caller_count: int, cross_community_edges: int) -> Tuple[float, List[str]]:
    factors: List[str] = []
    caller_score = min(caller_count / 10.0, 1.0)
    if caller_count > 0:
        factors.append(f"{caller_count} caller(s)")
    cc_score = min(cross_community_edges / 5.0, 1.0)
    if cross_community_edges > 0:
        factors.append(f"{cross_community_edges} cross-community edge(s)")
    score = round(0.6 * caller_score + 0.4 * cc_score, 3)
    return score, factors


class TestGapService:
    __test__ = False  # not a pytest class

    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def analyze(self, limit: int = 20) -> TestGapResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]
            return self._analyze(index, repo_root, limit)
        finally:
            index.close()

    def _analyze(
        self,
        index: ProjectIndex,
        repo_root: str,
        limit: int,
    ) -> TestGapResult:
        warnings: List[str] = []

        total_tested_by = index.conn.execute(
            "SELECT COUNT(*) AS cnt FROM edges WHERE relation = 'tested_by'"
        ).fetchone()["cnt"]
        if total_tested_by == 0:
            warnings.append(
                "No tested_by edges found. Run postprocess or verify the project has recognized test files."
            )

        all_symbols = index.conn.execute(
            """SELECT id, name, type, path, start_line, end_line, community_id
               FROM nodes
               WHERE type IN ('class','function','method')
                 AND is_test = 0""",
        ).fetchall()

        tested_ids = self._tested_symbol_ids(index)

        total_symbols = len(all_symbols)
        tested_count = 0
        untested_rows = []
        community_stats: Dict[int, List[int]] = {}  # community_id -> [total, tested]

        for row in all_symbols:
            cid = row["community_id"]
            if cid is not None:
                if cid not in community_stats:
                    community_stats[cid] = [0, 0]
                community_stats[cid][0] += 1

            if row["id"] in tested_ids:
                tested_count += 1
                if cid is not None:
                    community_stats[cid][1] += 1
            else:
                untested_rows.append(row)

        untested_count = total_symbols - tested_count
        coverage_pct = round((tested_count / total_symbols * 100) if total_symbols > 0 else 0.0, 1)

        hotspots = self._rank_hotspots(index, untested_rows, limit)

        untested_by_community: Dict[int, List[UntestedSymbol]] = {}
        for h in hotspots:
            if h.community_id is not None:
                untested_by_community.setdefault(h.community_id, []).append(h)

        community_coverage: List[CommunityCoverage] = []
        for cid in sorted(community_stats):
            total_c, tested_c = community_stats[cid]
            pct = round((tested_c / total_c * 100) if total_c > 0 else 0.0, 1)
            top_names = [h.name for h in untested_by_community.get(cid, [])[:3]]
            community_coverage.append(
                CommunityCoverage(
                    community_id=cid,
                    total_symbols=total_c,
                    tested_symbols=tested_c,
                    coverage_pct=pct,
                    untested_hotspots=top_names,
                )
            )

        parts = [f"{coverage_pct}% coverage ({tested_count}/{total_symbols} symbols tested)"]
        if hotspots:
            parts.append(f"{len(hotspots)} untested hotspot(s)")
        summary = ". ".join(parts) + "."

        return TestGapResult(
            command="test-gaps",
            db_path=self.db_path,
            repo_root=repo_root,
            total_symbols=total_symbols,
            tested_count=tested_count,
            untested_count=untested_count,
            coverage_pct=coverage_pct,
            hotspots=hotspots,
            community_coverage=community_coverage,
            summary=summary,
            warnings=warnings,
        )

    def _tested_symbol_ids(self, index: ProjectIndex) -> Set[str]:
        rows = index.conn.execute(
            """SELECT DISTINCT n.id
               FROM nodes n
               WHERE n.type IN ('class','function','method')
                 AND n.is_test = 0
                 AND (
                     EXISTS (SELECT 1 FROM edges e WHERE (e.source = n.id OR e.target = n.id) AND e.relation = 'tested_by')
                 )""",
        ).fetchall()
        return {r["id"] for r in rows}

    def _rank_hotspots(
        self,
        index: ProjectIndex,
        untested_rows: list,
        limit: int,
    ) -> List[UntestedSymbol]:
        scored: List[UntestedSymbol] = []
        for row in untested_rows:
            sym_id = row["id"]
            caller_count = index.conn.execute(
                "SELECT COUNT(*) AS cnt FROM edges WHERE target = ? AND relation IN ('calls','inherits')",
                (sym_id,),
            ).fetchone()["cnt"]

            community_id = row["community_id"]
            cross_community = 0
            if community_id is not None:
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

            hotspot_score, risk_factors = _compute_hotspot(caller_count, cross_community)

            start = row["start_line"]
            end = row["end_line"]
            line_range = [start, end] if start is not None and end is not None else None

            scored.append(
                UntestedSymbol(
                    id=sym_id,
                    name=row["name"],
                    kind=row["type"],
                    path=row["path"],
                    line_range=line_range,
                    caller_count=caller_count,
                    cross_community_edges=cross_community,
                    community_id=community_id,
                    hotspot_score=hotspot_score,
                    risk_factors=risk_factors,
                )
            )

        scored.sort(key=lambda s: (-s.hotspot_score, s.name))
        return scored[:limit]
