import json
from pathlib import Path

from csegraph import ContextService, IndexService
from csegraph_core.core.serializer import to_dict
from csegraph_core.retrieval.constants import VALID_REASONS


_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "context_contract_v1_shape.json"


def _write_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "helpers.py").write_text(
        "def clean_name(value: str) -> str:\n    return value.strip().lower()\n",
        encoding="utf-8",
    )
    (root / "service.py").write_text(
        "from helpers import clean_name\n\ndef create_user(name: str) -> dict:\n    return {'name': clean_name(name)}\n",
        encoding="utf-8",
    )


def test_context_json_contract_is_canonical_only(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement create_user with clean_name",
        target="create_user",
        profile="small",
    )
    payload = to_dict(context)
    expected = json.loads(_FIXTURE.read_text(encoding="utf-8"))

    assert payload["schema_version"] == expected["schema_version"]
    for key in expected["canonical_fields"]:
        assert key in payload
    for key in expected["sufficiency_fields"]:
        assert key in payload["sufficiency"]

    removed_fields = {
        "task",
        "target_node_id",
        "estimated_tokens",
        "metrics",
        "thresholds",
        "is_sufficient",
        "context_nodes",
    }
    assert removed_fields.isdisjoint(payload)

    assert payload["query"] == "Implement create_user with clean_name"
    assert payload["target"] == "symbol::service.py::function::create_user"
    assert payload["total_estimated_tokens"] >= 1
    assert payload["sufficiency"]["sufficient"] is True
    assert "semantic_overlap_relaxed" in payload["sufficiency"]["thresholds"]

    assert payload["nodes"][0]["id"] == "symbol::service.py::function::create_user"

    for node in payload["nodes"]:
        for key in expected["canonical_node_fields"]:
            assert key in node
        assert node["language"] == "python"
        assert set(node["reason"]).issubset(VALID_REASONS)
        assert "explanation" not in node


def test_context_json_contract_explanation_is_explain_only(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    explained = ContextService(db_path).build_context(
        task="Implement create_user with clean_name",
        target="create_user",
        profile="small",
        explain=True,
    )
    payload = to_dict(explained)

    assert payload["schema_version"] == "csegraph-context-v1"
    assert any("explanation" in node for node in payload["nodes"])
    assert "context_nodes" not in payload
