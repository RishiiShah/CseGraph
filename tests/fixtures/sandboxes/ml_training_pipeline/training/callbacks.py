from typing import Optional, Protocol


class Callback(Protocol):
    def on_epoch_end(self, epoch: int, loss: float, val_loss: float) -> bool:
        """Return True to continue, False to stop early."""


class EarlyStopping:
    def __init__(self, patience: int = 5, min_delta: float = 1e-4) -> None:
        self._patience = patience
        self._min_delta = min_delta
        self._best_loss = float("inf")
        self._wait = 0

    def on_epoch_end(self, epoch: int, loss: float, val_loss: float) -> bool:
        if val_loss < self._best_loss - self._min_delta:
            self._best_loss = val_loss
            self._wait = 0
        else:
            self._wait += 1
        return self._wait < self._patience

    def should_stop(self) -> bool:
        return self._wait >= self._patience


class LearningRateScheduler:
    def __init__(self, decay: float = 0.95) -> None:
        self._decay = decay

    def on_epoch_end(self, epoch: int, loss: float, val_loss: float) -> bool:
        return True

    def get_lr(self, initial_lr: float, epoch: int) -> float:
        return initial_lr * (self._decay ** epoch)


class CheckpointSaver:
    """Records best validation loss epoch."""

    def __init__(self) -> None:
        self._best_loss = float("inf")
        self._best_epoch = -1

    def on_epoch_end(self, epoch: int, loss: float, val_loss: float) -> bool:
        if val_loss < self._best_loss:
            self._best_loss = val_loss
            self._best_epoch = epoch
        return True

    @property
    def best_epoch(self) -> int:
        return self._best_epoch

    @property
    def best_loss(self) -> float:
        return self._best_loss
