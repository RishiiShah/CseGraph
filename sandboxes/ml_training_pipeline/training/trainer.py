from typing import Any, Dict, List

from ml_models.contracts import Model
from training.callbacks import Callback, EarlyStopping
from training.contracts import EpochLog, TrainingConfig
from training.optimizer import BaseOptimizer, SGDOptimizer


class ModelTrainer:
    def __init__(
        self,
        model: Model,
        config: TrainingConfig | None = None,
        optimizer: BaseOptimizer | None = None,
        callbacks: List[Callback] | None = None,
    ) -> None:
        self._model = model
        self._config = config or TrainingConfig()
        self._optimizer = optimizer or SGDOptimizer(lr=self._config.learning_rate)
        self._callbacks = callbacks or []
        self._logs: List[EpochLog] = []

    def train(
        self, X_train: List[List[float]], y_train: List[float]
    ) -> Dict[str, Any]:
        self._model.fit(X_train, y_train)
        preds = self._model.predict(X_train)
        loss = sum((p - y) ** 2 for p, y in zip(preds, y_train)) / len(y_train)
        self._logs.append(EpochLog(epoch=0, loss=loss))
        return {"loss": loss, "epochs_run": 1}

    def evaluate_step(self, X: List[List[float]], y: List[float]) -> float:
        preds = self._model.predict(X)
        return sum((p - yv) ** 2 for p, yv in zip(preds, y)) / len(y)

    @property
    def logs(self) -> List[EpochLog]:
        return list(self._logs)


def train(
    model: Model,
    X: List[List[float]],
    y: List[float],
    config: TrainingConfig | None = None,
) -> Dict[str, Any]:
    return ModelTrainer(model, config).train(X, y)


def evaluate_step(
    model: Model, X: List[List[float]], y: List[float]
) -> float:
    preds = model.predict(X)
    return sum((p - yv) ** 2 for p, yv in zip(preds, y)) / max(len(y), 1)
