from src.models import User


def save_user(user: User) -> str:
    return user.save()


def load_user(name: str) -> User:
    return User(name)
