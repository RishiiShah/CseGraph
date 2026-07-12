from src.models import Order


def handle(payload: dict[str, str]) -> str:
    return Order(payload["number"]).save()
