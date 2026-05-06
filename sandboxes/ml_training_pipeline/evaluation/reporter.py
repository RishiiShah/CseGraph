from typing import Any, Dict, List

from evaluation.contracts import EvalResult
from evaluation.evaluator import ModelEvaluator
from ml_models.contracts import Model


class EvaluationReport:
    def __init__(self, sandbox_name: str = "unnamed") -> None:
        self._sandbox_name = sandbox_name
        self._entries: List[EvalResult] = []

    def add_results(self, results: List[EvalResult]) -> None:
        self._entries.extend(results)

    def summary(self) -> Dict[str, Any]:
        return {
            "sandbox": self._sandbox_name,
            "metrics": {r.metric_name: r.value for r in self._entries},
            "count": len(self._entries),
        }


def format_report(results: List[EvalResult]) -> str:
    return "\n".join(f"{r.metric_name}: {r.value:.4f}" for r in results)


def summarize(
    model: Model, X: List[List[float]], y_true: List[float]
) -> Dict[str, float]:
    evaluator = ModelEvaluator()
    results = evaluator.evaluate(model, X, y_true)
    return evaluator.aggregate_metrics(results)
