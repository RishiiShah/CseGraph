from typing import Protocol


class PayloadFormatter(Protocol):
    def format(self, payload: dict) -> dict:
        """Format the final output payload."""


class ScoreProvider(Protocol):
    def score_for(self, user_id: str) -> int:
        """Return a deterministic score for a given user."""
