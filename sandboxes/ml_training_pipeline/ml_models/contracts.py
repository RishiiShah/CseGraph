from typing import List, Protocol


class Model(Protocol):
    def fit(self, X: List[List[float]], y: List[float]) -> None:
        """Train the model."""

    def predict(self, X: List[List[float]]) -> List[float]:
        """Return predictions for X."""


class Predictor(Protocol):
    def predict_single(self, features: List[float]) -> float:
        """Return a single prediction."""
