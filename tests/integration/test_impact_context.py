from __future__ import annotations

from pathlib import Path

import pytest

from csegraph import ContextRequest, ContextService, IndexService, to_dict


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "helpers.py").write_text(
        "def clean(value: str) -> str:\n    return value.strip()\n",
        encoding="utf-8",
    )
    (repo / "service.py").write_text(
        ("from helpers import clean\n\ndef create(name: str) -> str:\n    return clean(name)\n"),
        encoding="utf-8",
    )
    db = str(repo / ".csegraph" / "index.db")
    IndexService(db).index(repo)
    return repo, db


def _dispatch_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    src = repo / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "order_handlers.py").write_text(
        "from src.models import Order\n\n"
        "def handle(payload: dict[str, str]) -> str:\n"
        "    return Order(payload['number']).save()\n",
        encoding="utf-8",
    )
    (src / "user_handlers.py").write_text(
        "from src.service import create_user\n\n"
        "def handle(payload: dict[str, str]) -> str:\n"
        "    return create_user(payload['name'])\n",
        encoding="utf-8",
    )
    (src / "models.py").write_text(
        "class Order:\n"
        "    def __init__(self, number: str) -> None:\n"
        "        self.number = number\n\n"
        "    def save(self) -> str:\n"
        "        return f'order:{self.number}'\n",
        encoding="utf-8",
    )
    (src / "service.py").write_text(
        "def create_user(name: str) -> str:\n    return f'user:{name.lower()}'\n",
        encoding="utf-8",
    )
    (src / "router.py").write_text(
        "from collections.abc import Callable\n\n"
        "from src.order_handlers import handle as handle_order\n"
        "from src.user_handlers import handle as handle_user\n\n"
        "HANDLERS: dict[str, Callable[[dict[str, str]], str]] = {\n"
        "    'order': handle_order,\n"
        "    'user': handle_user,\n"
        "}\n\n"
        "def dispatch(kind: str, payload: dict[str, str]) -> str:\n"
        "    return HANDLERS[kind](payload)\n",
        encoding="utf-8",
    )
    (src / "workflow.py").write_text(
        "from src.router import dispatch\n\n"
        "def run(name: str) -> str:\n"
        "    return dispatch('user', {'name': name})\n",
        encoding="utf-8",
    )
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "test_router.py").write_text(
        "from src.router import dispatch\n\n"
        "def test_dispatches_user_payload() -> None:\n"
        "    assert dispatch('user', {'name': 'Ada'}) == 'user:ada'\n\n"
        "def test_dispatches_order_payload() -> None:\n"
        "    assert dispatch('order', {'number': 'A-1'}) == 'order:A-1'\n",
        encoding="utf-8",
    )
    db = str(repo / ".csegraph" / "index.db")
    IndexService(db).index(repo)
    return repo, db


def test_edit_task_uses_one_hop_impact_context(tmp_path: Path):
    repo, db = _repo(tmp_path)
    payload = to_dict(
        ContextService(db).retrieve(
            ContextRequest(
                repo=str(repo),
                task="Change create",
                target="create",
                task_kind="edit",
            )
        )
    )

    assert payload["status"] == "ready"
    assert payload["slices"][0]["symbol"] == "create"
    assert {slice_["symbol"] for slice_ in payload["slices"]} >= {"create", "clean"}


def test_low_budget_reports_missing_target_source(tmp_path: Path):
    repo, db = _repo(tmp_path)
    (repo / "large.py").write_text(
        "def large():\n" + "".join(f"    value_{i} = {i}\n" for i in range(400)),
        encoding="utf-8",
    )
    IndexService(db).index(repo)

    payload = to_dict(
        ContextService(db).retrieve(
            ContextRequest(
                repo=str(repo),
                task="Change large",
                target="large",
                task_kind="edit",
                token_budget=256,
            )
        )
    )

    assert payload["status"] == "insufficient"
    assert payload["slices"] == []
    assert payload["missing"][0]["kind"] == "target_source"


def test_dispatch_user_task_keeps_order_handlers_ahead_of_user_handlers(tmp_path: Path):
    repo, db = _dispatch_repo(tmp_path)
    payload = to_dict(
        ContextService(db).retrieve(
            ContextRequest(
                repo=str(repo),
                task="Assess one-hop test impact for dispatch on user handlers",
                target="dispatch",
                task_kind="test-impact",
            )
        )
    )

    assert payload["status"] == "ready"
    paths = [slice_["path"] for slice_ in payload["slices"]]
    assert "src/order_handlers.py" in paths
    if "src/user_handlers.py" in paths:
        assert paths.index("src/order_handlers.py") < paths.index("src/user_handlers.py")
    assert all(
        slice_["code"] == "" for slice_ in payload["slices"] if slice_["path"] != "src/router.py"
    )


def test_test_impact_keeps_test_role_when_test_also_calls_target(tmp_path: Path):
    repo, db = _dispatch_repo(tmp_path)
    payload = to_dict(
        ContextService(db).retrieve(
            ContextRequest(
                repo=str(repo),
                task="Assess one-hop test impact using tests for dispatch",
                target="dispatch",
                task_kind="test-impact",
            )
        )
    )

    assert any(
        slice_["path"] == "tests/test_router.py" and slice_["role"] == "test"
        for slice_ in payload["slices"]
    )


def test_task_kind_validation(tmp_path: Path):
    repo, db = _repo(tmp_path)
    with pytest.raises(ValueError, match="task_kind"):
        ContextService(db).retrieve(
            ContextRequest(repo=str(repo), task="Explain create", task_kind="invalid")
        )
