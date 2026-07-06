from src.service import create_user


def handle(payload: dict[str, str]) -> str:
    return create_user(payload["name"])
