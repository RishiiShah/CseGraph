from typing import List

from evaluation.contracts import EvalResult, Metric
from evaluation.metrics import MAE, MSE, Accuracy
from ml_models.contracts import Model


class ModelEvaluator:
    def __init__(self, metrics: List[Metric] | None = None) -> None:
        self._metrics = metrics or [Accuracy(), MSE(), MAE()]

    def evaluate(
        self, model: Model, X: List[List[float]], y_true: List[float]
    ) -> List[EvalResult]:
        y_pred = model.predict(X)
        return [
            EvalResult(metric_name=m.name, value=m.compute(y_true, y_pred))
            for m in self._metrics
        ]

    def aggregate_metrics(self, results: List[EvalResult]) -> dict:
        return {r.metric_name: r.value for r in results}


def evaluate(
    model: Model, X: List[List[float]], y_true: List[float]
) -> List[EvalResult]:
    return ModelEvaluator().evaluate(model, X, y_true)


def aggregate_metrics(results: List[EvalResult]) -> dict:
    return ModelEvaluator().aggregate_metrics(results)
