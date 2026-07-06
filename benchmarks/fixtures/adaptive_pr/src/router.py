from collections.abc import Callable

from src.order_handlers import handle as handle_order
from src.user_handlers import handle as handle_user

HANDLERS: dict[str, Callable[[dict[str, str]], str]] = {
    "order": handle_order,
    "user": handle_user,
}


def dispatch(kind: str, payload: dict[str, str]) -> str:
    return HANDLERS[kind](payload)
