from pathlib import Path

import pytest

from csegraph import ContextService, IndexService, RefreshService
from csegraph._core.core.serializer import to_dict


def _write_checkout_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pricing.py").write_text(
        "import requests\n\n"
        "def discount_for(customer_id: str) -> int:\n"
        "    response = requests.get(f'https://discounts/{customer_id}')\n"
        "    return int(response.json().get('discount', 0))\n\n"
        "def tax_for(subtotal: int) -> int:\n"
        "    return subtotal // 10\n\n"
        "def calculate_total(subtotal: int, customer_id: str) -> int:\n"
        "    discounted = subtotal - discount_for(customer_id)\n"
        "    return discounted + tax_for(discounted)\n",
        encoding="utf-8",
    )
    (root / "checkout.py").write_text(
        "from pricing import calculate_total\n\n"
        "def checkout(subtotal: int, customer_id: str) -> int:\n"
        "    return calculate_total(subtotal, customer_id)\n",
        encoding="utf-8",
    )
    (root / "orders.py").write_text(
        "from checkout import checkout\n\n"
        "def place_order(subtotal: int, customer_id: str) -> dict:\n"
        "    return {'total': checkout(subtotal, customer_id)}\n",
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_checkout.py").write_text(
        "from checkout import checkout\n"
        "from pricing import discount_for\n\n"
        "def test_checkout_applies_discount():\n"
        "    assert checkout(100, 'customer-1') == 99\n\n"
        "def test_discount_for_uses_service():\n"
        "    assert discount_for('customer-1') == 10\n",
        encoding="utf-8",
    )


@pytest.fixture
def checkout_index(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_checkout_repo(repo)
    IndexService(db_path).index(repo, profile="small")
    return repo, db_path


def test_edit_task_auto_promotes_and_returns_source_backed_impact(checkout_index):
    _, db_path = checkout_index

    result = ContextService(db_path).build_context(
        task="Change checkout to use a calculation with no discounts",
        target="checkout",
        profile="small",
        detail_level="auto",
    )
    payload = to_dict(result)

    assert payload["intent"] == "edit"
    assert payload["request"]["task_kind"] == "auto"
    assert payload["request"]["returned_detail_level"] == "standard"
    assert payload["sufficiency"]["edit_ready"] is True
    assert payload["missing_context"] == []
    assert payload["edit_targets"][0]["id"] == "symbol::checkout.py::function::checkout"
    assert payload["edit_targets"][0]["source_included"] is True

    symbols = {item["id"]: item for item in payload["symbols"]}
    assert "def checkout(" in symbols[payload["edit_targets"][0]["source_node_id"]]["source_text"]
    dependency_ids = {item["id"] for item in payload["impact"]["dependencies"]}
    assert "symbol::pricing.py::function::calculate_total" in dependency_ids
    assert "symbol::pricing.py::function::discount_for" in dependency_ids

    affected_test_ids = {item["id"] for item in payload["affected_tests"]}
    assert (
        "symbol::tests/test_checkout.py::function::test_checkout_applies_discount"
        in affected_test_ids
    )
    assert (
        "symbol::tests/test_checkout.py::function::test_discount_for_uses_service"
        in affected_test_ids
    )
    assert all(item["source_included"] for item in payload["affected_tests"])
    assertions_by_test = {
        item["id"]: item.get("assertions", []) for item in payload["affected_tests"]
    }
    assert any(
        "assert checkout(" in assertion["expression"]
        for assertion in assertions_by_test[
            "symbol::tests/test_checkout.py::function::test_checkout_applies_discount"
        ]
    )
    assert any(
        "assert discount_for(" in assertion["expression"]
        for assertion in assertions_by_test[
            "symbol::tests/test_checkout.py::function::test_discount_for_uses_service"
        ]
    )


def test_child_change_surfaces_callers_and_their_tests(checkout_index):
    _, db_path = checkout_index

    payload = to_dict(
        ContextService(db_path).build_context(
            task="Change discount_for response handling",
            target="discount_for",
            profile="small",
            task_kind="edit",
        )
    )

    dependent_ids = {item["id"] for item in payload["impact"]["dependents"]}
    assert "symbol::pricing.py::function::calculate_total" in dependent_ids
    assert "symbol::checkout.py::function::checkout" in dependent_ids
    affected_test_ids = {item["id"] for item in payload["affected_tests"]}
    assert (
        "symbol::tests/test_checkout.py::function::test_discount_for_uses_service"
        in affected_test_ids
    )
    assert (
        "symbol::tests/test_checkout.py::function::test_checkout_applies_discount"
        in affected_test_ids
    )


def test_task_kind_validation_and_test_impact_inference(checkout_index):
    _, db_path = checkout_index
    service = ContextService(db_path)

    with pytest.raises(ValueError, match="task_kind must be one of"):
        service.build_context(
            task="Inspect checkout",
            target="checkout",
            task_kind="invalid",
        )

    payload = to_dict(
        service.build_context(
            task="Which tests are affected by changes to checkout?",
            target="checkout",
            detail_level="auto",
        )
    )
    assert payload["intent"] == "test-impact"
    assert payload["request"]["returned_detail_level"] == "standard"
    assert payload["affected_tests"]
    assert payload["sufficiency"]["edit_ready"] is True


def test_low_budget_reports_exact_missing_edit_source(checkout_index):
    _, db_path = checkout_index

    payload = to_dict(
        ContextService(db_path).build_context(
            task="Remove discounts from checkout",
            target="checkout",
            profile="small",
            task_kind="edit",
            detail_level="auto",
            max_tokens=8,
        )
    )

    assert payload["request"]["returned_detail_level"] == "standard"
    assert payload["sufficiency"]["edit_ready"] is False
    assert payload["sufficiency"]["sufficient"] is False
    assert payload["missing_context"]
    assert {
        "kind",
        "node_id",
        "name",
        "path",
        "reason",
        "required_for",
    } <= set(payload["missing_context"][0])
    assert any(
        item["node_id"] == "symbol::checkout.py::function::checkout"
        and item["reason"] in {"node_not_returned", "token_budget"}
        for item in payload["missing_context"]
    )


def test_source_never_keeps_assertion_locations_without_expressions(checkout_index):
    _, db_path = checkout_index

    payload = to_dict(
        ContextService(db_path).build_context(
            task="Which tests change when checkout changes?",
            target="checkout",
            profile="small",
            task_kind="test-impact",
            detail_level="standard",
            include_source="never",
        )
    )

    assertions = [
        assertion
        for test in payload["affected_tests"]
        for assertion in test.get("assertions", [])
    ]
    assert assertions
    assert all("expression" not in assertion for assertion in assertions)
    assert all(assertion["line_range"] for assertion in assertions)


def test_renamed_child_returns_historical_broken_reference_impact(checkout_index):
    repo, db_path = checkout_index
    pricing = repo / "pricing.py"
    pricing.write_text(
        pricing.read_text(encoding="utf-8").replace(
            "def discount_for(",
            "def loyalty_adjustment(",
            1,
        ),
        encoding="utf-8",
    )
    RefreshService(db_path).refresh(
        profile="small",
        changed_paths=[pricing],
    )

    payload = to_dict(
        ContextService(db_path).build_context(
            task="Update callers after discount_for was renamed",
            target="discount_for",
            profile="small",
            task_kind="edit",
        )
    )

    replacement = "symbol::pricing.py::function::loyalty_adjustment"
    assert payload["target"]["resolution"] == "historical"
    assert payload["target"]["graph_target_id"] is None
    assert payload["sufficiency"]["edit_ready"] is False
    assert payload["missing_context"][0]["reason"] == "symbol_renamed"
    assert payload["missing_context"][0]["replaced_by"] == replacement
    assert {
        item["id"] for item in payload["impact"]["dependents"]
    } >= {"symbol::pricing.py::function::calculate_total"}
    assert {
        item["id"] for item in payload["affected_tests"]
    } >= {"symbol::tests/test_checkout.py::function::test_discount_for_uses_service"}
    assert payload["next_actions"][0]["arguments"]["target"] == replacement
