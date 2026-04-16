from dataclasses import dataclass, field
from typing import Any, Dict, List, Protocol


class Metric(Protocol):
    name: str

    def compute(self, y_true: List[float], y_pred: List[float]) -> float:
        """Compute metric value."""


@dataclass
class EvalResult:
    metric_name: str
    value: float
    extra: Dict[str, Any] = field(default_factory=dict)
