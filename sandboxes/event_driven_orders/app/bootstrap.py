from domain.order_service import OrderService, create_demo_order
from handlers.notification import on_order_created
from infrastructure.in_memory_bus import InMemoryEventBus
from infrastructure.in_memory_repo import InMemoryOrderRepository


class EventRecorder:
    def __init__(self):
        self.events: list[dict] = []

    def record(self, payload: dict) -> None:
        self.events.append(on_order_created(payload))


def run_demo() -> dict:
    bus = InMemoryEventBus()
    repo = InMemoryOrderRepository()
    recorder = EventRecorder()

    bus.subscribe("order_created", recorder.record)

    service = OrderService(repository=repo, event_bus=bus)
    order = create_demo_order(service)

    return {
        "order": order,
        "orders_in_repo": len(repo.list_all()),
        "events": recorder.events,
    }
