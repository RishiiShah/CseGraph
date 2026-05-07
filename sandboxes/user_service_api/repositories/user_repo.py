_USERS: dict[str, dict] = {}


def save_user(user_id: str, payload: dict) -> None:
    _USERS[user_id] = payload


def fetch_user(user_id: str) -> dict:
    return _USERS.get(user_id, {})


def list_users() -> list[dict]:
    return list(_USERS.values())
