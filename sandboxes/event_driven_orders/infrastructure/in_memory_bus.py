class InMemoryEventBus:
    def __init__(self):
        self._handlers: dict[str, list] = {}

    def subscribe(self, event_name: str, handler) -> None:
        self._handlers.setdefault(event_name, []).append(handler)

    def publish(self, event_name: str, payload: dict) -> None:
        for handler in self._handlers.get(event_name, []):
            handler(payload)
