from dataclasses import dataclass, field
from typing import Any, Dict, Protocol, List


class Trainer(Protocol):
    def train(
        self, X_train: List[List[float]], y_train: List[float]
    ) -> Dict[str, Any]:
        """Run training and return metrics."""


@dataclass
class TrainingConfig:
    learning_rate: float = 0.01
    epochs: int = 100
    patience: int = 5
    batch_size: int = 32
    model_type: str = "linear"


@dataclass
class EpochLog:
    epoch: int
    loss: float
    val_loss: float = 0.0
    extra: Dict[str, float] = field(default_factory=dict)
