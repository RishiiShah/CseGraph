from typing import Protocol

from repositories.user_repo import fetch_user, list_users, save_user
from utils.validators import is_valid_name, normalize_name, validate_user_id


class UserRepository(Protocol):
    def save(self, user_id: str, payload: dict) -> None:
        """Persist a user payload."""

    def fetch(self, user_id: str) -> dict:
        """Fetch a user by id."""

    def list_all(self) -> list[dict]:
        """Return all users."""


class InMemoryUserRepository:
    def save(self, user_id: str, payload: dict) -> None:
        save_user(user_id, payload)

    def fetch(self, user_id: str) -> dict:
        return fetch_user(user_id)

    def list_all(self) -> list[dict]:
        return list_users()


class UserService:
    def __init__(self, repository: UserRepository | None = None):
        self.repository = repository or InMemoryUserRepository()

    def create_user(self, user_id: str, name: str) -> bool:
        if not validate_user_id(user_id):
            return False

        normalized_name = normalize_name(name)
        if not is_valid_name(normalized_name):
            return False

        if self.repository.fetch(user_id):
            return False

        self.repository.save(user_id, {"id": user_id, "name": normalized_name})
        return True

    def get_user(self, user_id: str) -> dict:
        return self.repository.fetch(user_id)

    def get_user_summary(self) -> dict:
        users = self.repository.list_all()
        return {
            "count": len(users),
            "user_ids": sorted(user["id"] for user in users),
        }


def create_user(user_id: str, name: str) -> bool:
    return UserService().create_user(user_id, name)


def get_user(user_id: str) -> dict:
    return UserService().get_user(user_id)


def get_user_summary() -> dict:
    return UserService().get_user_summary()
