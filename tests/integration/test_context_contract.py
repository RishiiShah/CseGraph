import json
from pathlib import Path

from csegraph import ContextService, IndexService
from csegraph._core.core.serializer import to_dict
from csegraph._core.retrieval.constants import VALID_REASONS


_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "context_contract_v2_shape.json"


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

    assert set(payload) == {"schema_version", *expected["canonical_fields"]}

    assert payload["query"] == "Implement create_user with clean_name"
    assert payload["target"] == "symbol::service.py::function::create_user"
    assert payload["detail_level"] == "auto"
    assert payload["returned_detail_level"] == "minimal"
    assert payload["total_estimated_tokens"] >= 1
    assert payload["sufficiency"]["sufficient"] is True
    assert "semantic_overlap_relaxed" in payload["sufficiency"]["thresholds"]
    assert payload["next_actions"]
    assert any(action["action"] == "expand_context" for action in payload["next_actions"])
    assert payload["warnings"] == []

    assert payload["nodes"][0]["id"] == "symbol::service.py::function::create_user"

    assert payload["target_resolution"] == "resolved"
    assert payload["target_candidates"] == []

    for node in payload["nodes"]:
        for key in expected["canonical_node_fields"]:
            assert key in node
        assert node["language"] == "python"
        assert set(node["reason"]).issubset(VALID_REASONS)
        assert node["reason_details"]
        assert {d["code"] for d in node["reason_details"]}.issubset(VALID_REASONS)
        assert all("confidence_tier" in d and "score_contribution" in d for d in node["reason_details"])
        assert "source_text" not in node
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

    assert payload["schema_version"] == "csegraph-context-v2"
    assert any("explanation" in node for node in payload["nodes"])
    assert "context_nodes" not in payload


def test_context_standard_detail_includes_selected_source(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement create_user with clean_name",
        target="create_user",
        profile="small",
        detail_level="standard",
    )
    payload = to_dict(context)

    assert payload["detail_level"] == "standard"
    assert payload["returned_detail_level"] == "standard"
    assert any("source_text" in node for node in payload["nodes"])
    assert any(
        node["id"] == "symbol::service.py::function::create_user"
        and "def create_user(name: str) -> dict:" in node.get("source_text", "")
        for node in payload["nodes"]
    )


def test_context_full_detail_includes_explanations_and_source(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement create_user with clean_name",
        target="create_user",
        profile="small",
        detail_level="full",
    )
    payload = to_dict(context)

    assert payload["detail_level"] == "full"
    assert payload["returned_detail_level"] == "full"
    assert all("source_text" in node for node in payload["nodes"])
    assert any("explanation" in node for node in payload["nodes"])


def test_context_auto_upgrades_to_standard_when_sufficiency_fails(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_repo(repo)
    (repo / "csegraph.json").write_text(
        json.dumps({"semantic_threshold_relaxed": 1.1}),
        encoding="utf-8",
    )
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement create_user with clean_name",
        target="create_user",
        profile="small",
        detail_level="auto",
    )
    payload = to_dict(context)

    assert payload["detail_level"] == "auto"
    assert payload["returned_detail_level"] == "standard"
    assert payload["sufficiency"]["sufficient"] is False
    assert any("source_text" in node for node in payload["nodes"])


def test_context_minimal_detail_never_includes_source(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="Implement create_user with clean_name",
        target="create_user",
        profile="small",
        detail_level="minimal",
        include_source="always",
        explain=True,
    )
    payload = to_dict(context)

    assert payload["detail_level"] == "minimal"
    assert payload["returned_detail_level"] == "minimal"
    assert all("source_text" not in node for node in payload["nodes"])
    assert any("explanation" in node for node in payload["nodes"])


def test_minimal_detail_truncates_long_summaries(tmp_path):
    import sqlite3
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    repo.mkdir(parents=True, exist_ok=True)

    (repo / "module.py").write_text(
        "def long_documented():\n    return 42\n",
        encoding="utf-8",
    )

    IndexService(db_path).index(repo, profile="small")

    # Directly insert a long summary into the database to ensure deterministic test
    node_id = "symbol::module.py::function::long_documented"
    long_summary = " ".join(f"token{i}" for i in range(80))
    assert len(long_summary) > 240

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE summaries SET summary = ? WHERE node_id = ?",
            (long_summary, node_id),
        )

    # Test minimal detail truncates the summary
    minimal = ContextService(db_path).build_context(
        task="long_documented",
        target="long_documented",
        profile="small",
        detail_level="minimal",
    )
    minimal_payload = to_dict(minimal)

    # Test standard detail keeps the full summary
    standard = ContextService(db_path).build_context(
        task="long_documented",
        target="long_documented",
        profile="small",
        detail_level="standard",
    )
    standard_payload = to_dict(standard)

    minimal_target = next(n for n in minimal_payload["nodes"] if n["id"] == node_id)
    standard_target = next(n for n in standard_payload["nodes"] if n["id"] == node_id)

    # Minimal summary should be truncated and end with "..."
    assert len(minimal_target["summary"]) <= 240
    assert minimal_target["summary"].endswith("...")

    # Standard summary should be full and not truncated
    assert standard_target["summary"] == long_summary


def test_auto_detail_upgrades_to_standard_when_token_budget_breaks_sufficiency(tmp_path):
    """Auto detail should upgrade to standard if token trimming breaks sufficiency."""
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    # Call with auto detail and extremely tight token budget (1 token)
    # This should start with minimal but upgrade to standard if it breaks sufficiency
    context = ContextService(db_path).build_context(
        task="Implement create_user with clean_name",
        target="create_user",
        profile="small",
        detail_level="auto",
        max_tokens=1,
    )
    payload = to_dict(context)

    # With such a tight budget, we should have upgraded to standard to try to improve
    assert payload["detail_level"] == "auto"
    assert payload["returned_detail_level"] == "standard"
    assert payload["sufficiency"]["sufficient"] is False
    assert payload["warnings"] == ["Context sufficiency thresholds were not met."]
