from src.math_ops import normalize
from src.models import User
from src.repository import save_user


def create_user(name: str) -> str:
    user = User(normalize(name))
    return save_user(user)


def display_name(name: str) -> str:
    return normalize(name).title()
