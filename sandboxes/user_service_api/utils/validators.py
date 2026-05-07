def normalize_name(name: str) -> str:
    return " ".join(part.capitalize() for part in name.split())


def validate_user_id(user_id: str) -> bool:
    return user_id.startswith("u-") and len(user_id) > 2


def is_valid_name(name: str) -> bool:
    return bool(name and name.strip())
