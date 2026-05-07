"""Unit tests for the event_driven_orders sandbox."""
import os
import sys

SANDBOX_PATH = os.environ.get(
    "SANDBOX_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "sandboxes", "event_driven_orders"),
)
sys.path.insert(0, os.path.abspath(SANDBOX_PATH))

from domain.order_service import OrderService


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class _MockRepo:
    def __init__(self):
        self.orders: list = []

    def save(self, order: dict) -> None:
        self.orders.append(order)

    def list_all(self) -> list:
        return self.orders


class _MockBus:
    def __init__(self):
        self.events: list = []

    def subscribe(self, event_name: str, handler) -> None:
        pass

    def publish(self, event_name: str, payload: dict) -> None:
        self.events.append((event_name, payload))


# ---------------------------------------------------------------------------
# create_order return value
# ---------------------------------------------------------------------------

def test_create_order_returns_dict():
    service = OrderService(_MockRepo(), _MockBus())
    order = service.create_order("cust-1", 99.0)
    assert isinstance(order, dict)


def test_create_order_has_order_id():
    service = OrderService(_MockRepo(), _MockBus())
    order = service.create_order("cust-1", 99.0)
    assert "order_id" in order


def test_create_order_customer_id_matches():
    service = OrderService(_MockRepo(), _MockBus())
    order = service.create_order("cust-42", 50.0)
    assert order["customer_id"] == "cust-42"


def test_create_order_amount_matches():
    service = OrderService(_MockRepo(), _MockBus())
    order = service.create_order("cust-1", 199.99)
    assert order["amount"] == 199.99


def test_create_order_status_is_created():
    service = OrderService(_MockRepo(), _MockBus())
    order = service.create_order("cust-1", 10.0)
    assert order["status"] == "created"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def test_create_order_persists_to_repo():
    repo = _MockRepo()
    service = OrderService(repo, _MockBus())
    service.create_order("cust-1", 99.0)
    assert len(repo.orders) == 1


def test_create_order_persisted_payload_matches():
    repo = _MockRepo()
    service = OrderService(repo, _MockBus())
    service.create_order("cust-7", 77.0)
    assert repo.orders[0]["customer_id"] == "cust-7"
    assert repo.orders[0]["amount"] == 77.0


def test_multiple_orders_all_persisted():
    repo = _MockRepo()
    service = OrderService(repo, _MockBus())
    service.create_order("c1", 10.0)
    service.create_order("c2", 20.0)
    assert len(repo.orders) == 2


# ---------------------------------------------------------------------------
# Event publishing
# ---------------------------------------------------------------------------

def test_create_order_publishes_one_event():
    bus = _MockBus()
    service = OrderService(_MockRepo(), bus)
    service.create_order("cust-1", 99.0)
    assert len(bus.events) == 1


def test_create_order_event_name_is_order_created():
    bus = _MockBus()
    service = OrderService(_MockRepo(), bus)
    service.create_order("cust-1", 99.0)
    assert bus.events[0][0] == "order_created"


def test_create_order_event_payload_contains_order():
    bus = _MockBus()
    service = OrderService(_MockRepo(), bus)
    order = service.create_order("cust-5", 55.0)
    event_payload = bus.events[0][1]
    assert event_payload["customer_id"] == "cust-5"
    assert event_payload["amount"] == 55.0
