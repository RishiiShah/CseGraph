from .contracts import EventBus, OrderRepository


class OrderService:
    def __init__(self, repository: OrderRepository, event_bus: EventBus):
        self.repository = repository
        self.event_bus = event_bus

    def create_order(self, customer_id: str, amount: float) -> dict:
        order = {
            "order_id": f"o-{customer_id}",
            "customer_id": customer_id,
            "amount": amount,
            "status": "created",
        }
        self.repository.save(order)
        self.event_bus.publish("order_created", order)
        return order


def create_demo_order(service: OrderService) -> dict:
    return service.create_order("user-42", 199.0)
