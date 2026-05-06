from typing import List

from ml_models.contracts import Model


class LinearRegressor:
    """Ordinary least squares via gradient descent."""

    def __init__(self, lr: float = 0.01, epochs: int = 100) -> None:
        self._lr = lr
        self._epochs = epochs
        self._weights: List[float] = []
        self._bias: float = 0.0

    def fit(self, X: List[List[float]], y: List[float]) -> None:
        n_features = len(X[0]) if X else 0
        self._weights = [0.0] * n_features
        self._bias = 0.0
        n = len(X)
        for _ in range(self._epochs):
            preds = self.predict(X)
            errors = [preds[i] - y[i] for i in range(n)]
            self._bias -= self._lr * sum(errors) / n
            for j in range(n_features):
                grad = sum(errors[i] * X[i][j] for i in range(n)) / n
                self._weights[j] -= self._lr * grad

    def predict(self, X: List[List[float]]) -> List[float]:
        return [
            self._bias + sum(w * xi for w, xi in zip(self._weights, row))
            for row in X
        ]

    def predict_single(self, features: List[float]) -> float:
        return self.predict([features])[0]


def fit_linear(
    X: List[List[float]], y: List[float], lr: float = 0.01, epochs: int = 100
) -> LinearRegressor:
    m = LinearRegressor(lr=lr, epochs=epochs)
    m.fit(X, y)
    return m


def predict_linear(model: LinearRegressor, X: List[List[float]]) -> List[float]:
    return model.predict(X)
