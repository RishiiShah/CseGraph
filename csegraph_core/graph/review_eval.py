"""Evaluation harness: measures precision/recall of review intelligence."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Set

from csegraph_core.graph.change_detection import ChangeDetectionService
from csegraph_core.graph.review_questions import ReviewQuestionsService
from csegraph_core.index.repository import ProjectIndex


@dataclass
class RiskLevelMetrics:
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float


@dataclass
class ReviewEvalResult:
    command: str
    db_path: str
    repo_root: str
    base_ref: str
    ground_truth_count: int
    detected_count: int
    overall_precision: float
    overall_recall: float
    overall_f1: float
    high_risk: RiskLevelMetrics
    medium_risk: RiskLevelMetrics
    low_risk: RiskLevelMetrics
    missed_symbols: List[str]
    false_alarm_symbols: List[str]
    question_coverage: float
    summary: str
    warnings: List[str] = field(default_factory=list)


def _calc_metrics(tp: int, fp: int, fn: int) -> RiskLevelMetrics:
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return RiskLevelMetrics(
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=round(precision, 3),
        recall=round(recall, 3),
        f1=round(f1, 3),
    )


class ReviewEvalService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def evaluate(
        self,
        ground_truth_ids: List[str],
        base_ref: str = "HEAD~1",
        risk_threshold: str = "medium",
    ) -> ReviewEvalResult:
        warnings: List[str] = []

        gt_set = set(ground_truth_ids)
        self._validate_ids(gt_set, warnings)

        change_result = ChangeDetectionService(self.db_path).detect_changes(base_ref=base_ref)
        warnings.extend(change_result.warnings)

        high_ids = {s.id for s in change_result.high_risk}
        medium_ids = {s.id for s in change_result.medium_risk}
        low_ids = {s.id for s in change_result.low_risk}
        all_detected = high_ids | medium_ids | low_ids

        if risk_threshold == "high":
            detected_positive = high_ids
        elif risk_threshold == "low":
            detected_positive = all_detected
        else:
            detected_positive = high_ids | medium_ids

        overall = _calc_metrics(
            tp=len(detected_positive & gt_set),
            fp=len(detected_positive - gt_set),
            fn=len(gt_set - detected_positive),
        )

        high_metrics = _calc_metrics(
            tp=len(high_ids & gt_set),
            fp=len(high_ids - gt_set),
            fn=len(gt_set - high_ids),
        )
        medium_metrics = _calc_metrics(
            tp=len(medium_ids & gt_set),
            fp=len(medium_ids - gt_set),
            fn=len(gt_set - medium_ids),
        )
        low_metrics = _calc_metrics(
            tp=len(low_ids & gt_set),
            fp=len(low_ids - gt_set),
            fn=len(gt_set - low_ids),
        )

        missed = sorted(gt_set - all_detected)
        false_alarms = sorted(high_ids - gt_set)

        question_coverage = self._question_coverage(gt_set, base_ref, warnings)

        parts = [
            f"P={overall.precision:.3f} R={overall.recall:.3f} F1={overall.f1:.3f}",
            f"{len(gt_set)} ground-truth, {len(detected_positive)} detected",
        ]
        if missed:
            parts.append(f"{len(missed)} missed")
        summary = ". ".join(parts) + "."

        return ReviewEvalResult(
            command="review-eval",
            db_path=self.db_path,
            repo_root=change_result.repo_root,
            base_ref=base_ref,
            ground_truth_count=len(gt_set),
            detected_count=len(detected_positive),
            overall_precision=overall.precision,
            overall_recall=overall.recall,
            overall_f1=overall.f1,
            high_risk=high_metrics,
            medium_risk=medium_metrics,
            low_risk=low_metrics,
            missed_symbols=missed,
            false_alarm_symbols=false_alarms,
            question_coverage=question_coverage,
            summary=summary,
            warnings=warnings,
        )

    def _validate_ids(self, gt_set: Set[str], warnings: List[str]) -> None:
        if not gt_set:
            return
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            for gid in sorted(gt_set):
                exists = index.conn.execute(
                    "SELECT 1 FROM nodes WHERE id = ?", (gid,)
                ).fetchone()
                if exists is None:
                    warnings.append(f"Ground-truth ID not in index: {gid}")
        finally:
            index.close()

    def _question_coverage(
        self, gt_set: Set[str], base_ref: str, warnings: List[str],
    ) -> float:
        if not gt_set:
            return 0.0
        try:
            q_result = ReviewQuestionsService(self.db_path).generate(base_ref=base_ref)
        except Exception:
            warnings.append("Could not generate review questions for coverage check.")
            return 0.0

        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            name_to_ids: dict[str, set[str]] = {}
            for gid in gt_set:
                row = index.conn.execute(
                    "SELECT name FROM nodes WHERE id = ?", (gid,)
                ).fetchone()
                if row:
                    name_to_ids.setdefault(row["name"], set()).add(gid)
        finally:
            index.close()

        covered_ids: Set[str] = set()
        for q in q_result.questions:
            for sym_name in q.related_symbols:
                covered_ids.update(name_to_ids.get(sym_name, set()))

        return round(len(covered_ids & gt_set) / len(gt_set), 3)
