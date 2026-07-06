from src.router import dispatch


def test_dispatches_user_payload() -> None:
    assert dispatch("user", {"name": "Ada"}) == "user:ada"


def test_dispatches_order_payload() -> None:
    assert dispatch("order", {"number": "A-1"}) == "order:A-1"
