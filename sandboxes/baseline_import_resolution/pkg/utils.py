def helper() -> str:
    return "ok"


def sanitize_user_id(user_id: str) -> str:
    return user_id.strip().lower()


def process() -> int:
    return 1


def caller() -> int:
    return process()
