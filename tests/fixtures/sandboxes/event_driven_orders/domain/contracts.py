from typing import Protocol


class EventBus(Protocol):
    def subscribe(self, event_name: str, handler) -> None:
        """Register event handler."""

    def publish(self, event_name: str, payload: dict) -> None:
        """Emit event payload."""


class OrderRepository(Protocol):
    def save(self, order: dict) -> None:
        """Save order payload."""

    def list_all(self) -> list[dict]:
        """List all orders."""
