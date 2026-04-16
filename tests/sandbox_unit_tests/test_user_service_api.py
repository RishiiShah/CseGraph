"""Unit tests for the user_service_api sandbox."""
import os
import sys

SANDBOX_PATH = os.environ.get(
    "SANDBOX_PATH",
    os.path.join(os.path.dirname(__file__), "..", "fixtures", "sandboxes", "user_service_api"),
)
sys.path.insert(0, os.path.abspath(SANDBOX_PATH))

from utils.validators import normalize_name, validate_user_id, is_valid_name
from services.user_service import UserService


# ---------------------------------------------------------------------------
# In-memory repository stub (avoids the global dict in user_repo.py)
# ---------------------------------------------------------------------------

class _MockRepo:
    def __init__(self):
        self._store: dict = {}

    def save(self, user_id: str, payload: dict) -> None:
        self._store[user_id] = payload

    def fetch(self, user_id: str) -> dict:
        return self._store.get(user_id, {})

    def list_all(self) -> list:
        return list(self._store.values())


# ---------------------------------------------------------------------------
# normalize_name
# ---------------------------------------------------------------------------

def test_normalize_name_capitalizes_each_word():
    assert normalize_name("alice smith") == "Alice Smith"


def test_normalize_name_single_word():
    assert normalize_name("bob") == "Bob"


def test_normalize_name_already_capitalized():
    assert normalize_name("Alice") == "Alice"


def test_normalize_name_extra_spaces_collapsed():
    result = normalize_name("alice  smith")
    # split() handles multiple spaces; each part is capitalized
    assert "Alice" in result
    assert "Smith" in result


# ---------------------------------------------------------------------------
# validate_user_id
# ---------------------------------------------------------------------------

def test_validate_user_id_valid():
    assert validate_user_id("u-123") is True


def test_validate_user_id_too_short():
    # "u-" has length 2, not > 2
    assert validate_user_id("u-") is False


def test_validate_user_id_wrong_prefix():
    assert validate_user_id("x-123") is False


def test_validate_user_id_no_prefix():
    assert validate_user_id("123") is False


# ---------------------------------------------------------------------------
# is_valid_name
# ---------------------------------------------------------------------------

def test_is_valid_name_non_empty():
    assert is_valid_name("Alice") is True


def test_is_valid_name_empty_string():
    assert is_valid_name("") is False


def test_is_valid_name_whitespace_only():
    assert is_valid_name("   ") is False


# ---------------------------------------------------------------------------
# UserService.create_user
# ---------------------------------------------------------------------------

def test_create_user_success():
    service = UserService(repository=_MockRepo())
    assert service.create_user("u-001", "alice smith") is True


def test_create_user_stores_normalized_name():
    repo = _MockRepo()
    service = UserService(repository=repo)
    service.create_user("u-001", "alice smith")
    assert repo.fetch("u-001")["name"] == "Alice Smith"


def test_create_user_invalid_id_fails():
    service = UserService(repository=_MockRepo())
    assert service.create_user("bad_id", "Alice") is False


def test_create_user_empty_name_fails():
    service = UserService(repository=_MockRepo())
    assert service.create_user("u-001", "  ") is False


def test_create_user_duplicate_fails():
    repo = _MockRepo()
    service = UserService(repository=repo)
    service.create_user("u-001", "Alice")
    assert service.create_user("u-001", "Alice") is False


# ---------------------------------------------------------------------------
# UserService.get_user
# ---------------------------------------------------------------------------

def test_get_user_returns_stored_payload():
    repo = _MockRepo()
    service = UserService(repository=repo)
    service.create_user("u-001", "Alice")
    user = service.get_user("u-001")
    assert user["id"] == "u-001"


def test_get_user_missing_returns_empty():
    service = UserService(repository=_MockRepo())
    assert service.get_user("u-999") == {}


# ---------------------------------------------------------------------------
# UserService.get_user_summary
# ---------------------------------------------------------------------------

def test_get_user_summary_count():
    repo = _MockRepo()
    service = UserService(repository=repo)
    service.create_user("u-001", "Alice")
    service.create_user("u-002", "Bob")
    summary = service.get_user_summary()
    assert summary["count"] == 2


def test_get_user_summary_ids_sorted():
    repo = _MockRepo()
    service = UserService(repository=repo)
    service.create_user("u-002", "Bob")
    service.create_user("u-001", "Alice")
    summary = service.get_user_summary()
    assert summary["user_ids"] == ["u-001", "u-002"]


def test_get_user_summary_empty():
    service = UserService(repository=_MockRepo())
    summary = service.get_user_summary()
    assert summary["count"] == 0
    assert summary["user_ids"] == []
