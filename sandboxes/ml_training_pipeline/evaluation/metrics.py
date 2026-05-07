from typing import List

from evaluation.contracts import Metric


class Accuracy:
    name = "accuracy"

    def compute(self, y_true: List[float], y_pred: List[float]) -> float:
        if not y_true:
            return 0.0
        correct = sum(1 for t, p in zip(y_true, y_pred) if round(t) == round(p))
        return correct / len(y_true)


class MSE:
    name = "mse"

    def compute(self, y_true: List[float], y_pred: List[float]) -> float:
        if not y_true:
            return 0.0
        return sum((t - p) ** 2 for t, p in zip(y_true, y_pred)) / len(y_true)


class MAE:
    name = "mae"

    def compute(self, y_true: List[float], y_pred: List[float]) -> float:
        if not y_true:
            return 0.0
        return sum(abs(t - p) for t, p in zip(y_true, y_pred)) / len(y_true)


def compute_accuracy(y_true: List[float], y_pred: List[float]) -> float:
    return Accuracy().compute(y_true, y_pred)


def compute_mse(y_true: List[float], y_pred: List[float]) -> float:
    return MSE().compute(y_true, y_pred)
