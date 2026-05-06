from typing import List


class BaseOptimizer:
    def __init__(self, lr: float = 0.01) -> None:
        self._lr = lr
        self._step_count = 0

    def step(self, weights: List[float], gradients: List[float]) -> List[float]:
        raise NotImplementedError

    @property
    def learning_rate(self) -> float:
        return self._lr


class SGDOptimizer(BaseOptimizer):
    def __init__(self, lr: float = 0.01, momentum: float = 0.0) -> None:
        super().__init__(lr)
        self._momentum = momentum
        self._velocity: List[float] = []

    def step(self, weights: List[float], gradients: List[float]) -> List[float]:
        if not self._velocity:
            self._velocity = [0.0] * len(weights)
        self._step_count += 1
        updated = []
        for i, (w, g) in enumerate(zip(weights, gradients)):
            self._velocity[i] = self._momentum * self._velocity[i] + g
            updated.append(w - self._lr * self._velocity[i])
        return updated


class AdamOptimizer(BaseOptimizer):
    def __init__(
        self,
        lr: float = 0.001,
        beta1: float = 0.9,
        beta2: float = 0.999,
        eps: float = 1e-8,
    ) -> None:
        super().__init__(lr)
        self._beta1 = beta1
        self._beta2 = beta2
        self._eps = eps
        self._m: List[float] = []
        self._v: List[float] = []

    def step(self, weights: List[float], gradients: List[float]) -> List[float]:
        if not self._m:
            self._m = [0.0] * len(weights)
            self._v = [0.0] * len(weights)
        self._step_count += 1
        t = self._step_count
        updated = []
        for i, (w, g) in enumerate(zip(weights, gradients)):
            self._m[i] = self._beta1 * self._m[i] + (1 - self._beta1) * g
            self._v[i] = self._beta2 * self._v[i] + (1 - self._beta2) * g * g
            m_hat = self._m[i] / (1 - self._beta1 ** t)
            v_hat = self._v[i] / (1 - self._beta2 ** t)
            updated.append(w - self._lr * m_hat / (v_hat ** 0.5 + self._eps))
        return updated
