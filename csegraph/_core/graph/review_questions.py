"""Review question generation from graph structure and change detection."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Set, Tuple

from csegraph._core.graph.change_detection import ChangeDetectionService, ChangedSymbol
from csegraph._core.index.repository import ProjectIndex


@dataclass
class ReviewQuestion:
    question: str
    category: str
    priority: int
    related_symbols: List[str] = field(default_factory=list)


@dataclass
class ReviewQuestionsResult:
    command: str
    db_path: str
    repo_root: str
    base_ref: str
    total_questions: int
    questions: List[ReviewQuestion]
    summary: str
    warnings: List[str] = field(default_factory=list)


_MAX_QUESTIONS = 10


class ReviewQuestionsService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def generate(self, base_ref: str = "HEAD~1") -> ReviewQuestionsResult:
        change_result = ChangeDetectionService(self.db_path).detect_changes(base_ref=base_ref)

        questions: List[ReviewQuestion] = []
        seen: Set[Tuple[str, str]] = set()  # (category, symbol_name)

        def _add(q: ReviewQuestion) -> None:
            key = (q.category, ",".join(sorted(q.related_symbols)))
            if key in seen:
                return
            seen.add(key)
            questions.append(q)

        for sym in change_result.high_risk:
            if not sym.has_test_coverage:
                _add(ReviewQuestion(
                    question=(
                        f"'{sym.name}' is high-risk ({sym.caller_count} callers) "
                        f"and has no test coverage. What test should verify this change?"
                    ),
                    category="test_gap",
                    priority=1,
                    related_symbols=[sym.name],
                ))

        for sym in change_result.high_risk + change_result.medium_risk:
            if sym.cross_community_edges > 0:
                _add(ReviewQuestion(
                    question=(
                        f"'{sym.name}' touches {sym.cross_community_edges} "
                        f"cross-community edge(s). Have dependent communities been notified?"
                    ),
                    category="cross_community",
                    priority=1,
                    related_symbols=[sym.name],
                ))

        for sym in change_result.high_risk:
            if sym.caller_count >= 5:
                caller_names = self._caller_names(sym.id, limit=5)
                callers_text = ""
                if caller_names:
                    callers_text = f" Callers: {', '.join(caller_names)}."
                _add(ReviewQuestion(
                    question=(
                        f"'{sym.name}' has {sym.caller_count} callers. "
                        f"Have all callers been checked for breakage?{callers_text}"
                    ),
                    category="blast_radius",
                    priority=2,
                    related_symbols=[sym.name] + caller_names,
                ))

        for sym in change_result.medium_risk:
            if not sym.has_test_coverage:
                _add(ReviewQuestion(
                    question=(
                        f"'{sym.name}' is untested. "
                        f"Will this change be exercised by any existing integration test?"
                    ),
                    category="test_gap",
                    priority=2,
                    related_symbols=[sym.name],
                ))

        if change_result.communities_affected >= 2:
            _add(ReviewQuestion(
                question=(
                    f"Changes span {change_result.communities_affected} communities. "
                    f"Does this PR belong in a single unit, or should it be split?"
                ),
                category="cross_community",
                priority=3,
                related_symbols=[],
            ))

        questions.sort(key=lambda q: (q.priority, q.category))
        questions = questions[:_MAX_QUESTIONS]

        total = len(questions)
        if total == 0:
            summary = "No review questions generated."
        else:
            summary = f"{total} review question(s) generated."

        return ReviewQuestionsResult(
            command="review-questions",
            db_path=self.db_path,
            repo_root=change_result.repo_root,
            base_ref=base_ref,
            total_questions=total,
            questions=questions,
            summary=summary,
            warnings=change_result.warnings,
        )

    def _caller_names(self, sym_id: str, limit: int = 5) -> List[str]:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            rows = index.conn.execute(
                """SELECT n.name FROM edges e
                   JOIN nodes n ON n.id = e.source
                   WHERE e.target = ? AND e.relation IN ('calls','inherits')
                   LIMIT ?""",
                (sym_id, limit),
            ).fetchall()
            return [r["name"] for r in rows]
        finally:
            index.close()
