class InMemoryOrderRepository:
    def __init__(self):
        self._orders: list[dict] = []

    def save(self, order: dict) -> None:
        self._orders.append(order)

    def list_all(self) -> list[dict]:
        return list(self._orders)
