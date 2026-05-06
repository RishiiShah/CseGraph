from typing import List

from ml_models.contracts import Model
from ml_models.linear import LinearRegressor
from ml_models.tree import DecisionTreeClassifier


class EnsembleModel:
    """Simple averaging ensemble."""

    def __init__(self, models: List[Model]) -> None:
        self._models = models

    def fit(self, X: List[List[float]], y: List[float]) -> None:
        for m in self._models:
            m.fit(X, y)

    def predict(self, X: List[List[float]]) -> List[float]:
        if not self._models:
            return [0.0] * len(X)
        all_preds = [m.predict(X) for m in self._models]
        n = len(X)
        k = len(self._models)
        return [sum(all_preds[mi][i] for mi in range(k)) / k for i in range(n)]


class VotingEnsemble(EnsembleModel):
    """Majority-vote ensemble for classifiers."""

    def predict(self, X: List[List[float]]) -> List[float]:
        if not self._models:
            return [0.0] * len(X)
        all_preds = [m.predict(X) for m in self._models]
        n = len(X)
        result = []
        for i in range(n):
            votes = [all_preds[mi][i] for mi in range(len(self._models))]
            result.append(max(set(votes), key=votes.count))
        return result


def predict_ensemble(models: List[Model], X: List[List[float]]) -> List[float]:
    return EnsembleModel(models).predict(X)
