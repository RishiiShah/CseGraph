import json
from pathlib import Path

import csegraph._core.retrieval.context as context_module
from csegraph import ContextService, IndexService
from csegraph._core.core.models import ContextRelationship
from csegraph._core.core.serializer import to_dict
from csegraph._core.retrieval.constants import VALID_REASONS

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "context_contract_v3_shape.json"


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


def _write_auth_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "passwords.py").write_text(
        "def verify_password(password: str, password_hash: str) -> bool:\n"
        "    return password and password_hash.endswith(password)\n\n"
        "def rotate_password(user_id: str) -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )
    (root / "sessions.py").write_text(
        "def issue_token(user_id: str) -> str:\n"
        "    return f'token:{user_id}'\n\n"
        "def revoke_token(token: str) -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )
    (root / "auth.py").write_text(
        "from passwords import verify_password\n"
        "from sessions import issue_token\n\n"
        "def authenticate_user(username: str, password: str) -> str | None:\n"
        "    user = load_user(username)\n"
        "    if not verify_password(password, user['password_hash']):\n"
        "        return None\n"
        "    return issue_token(user['id'])\n\n"
        "def load_user(username: str) -> dict:\n"
        "    return {'id': username, 'password_hash': f'hash:{username}'}\n\n"
        "def audit_login(username: str) -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )
    (root / "routes.py").write_text(
        "from auth import authenticate_user\n\n"
        "def login(request: dict) -> str | None:\n"
        "    return authenticate_user(request['username'], request['password'])\n\n"
        "def public_status() -> str:\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )
    (root / "middleware.py").write_text(
        "from auth import authenticate_user\n\n"
        "def require_auth(request: dict) -> bool:\n"
        "    return authenticate_user(request['username'], request['password']) is not None\n",
        encoding="utf-8",
    )
    (root / "noise.py").write_text(
        "\n\n".join(
            f"def irrelevant_helper_{index}() -> int:\n    return {index}" for index in range(12)
        )
        + "\n",
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

    assert payload["request"]["task"] == "Implement create_user with clean_name"
    assert payload["target"]["id"] == "symbol::service.py::function::create_user"
    assert payload["target"]["graph_target_id"] == "symbol::service.py::function::create_user"
    assert payload["target"]["display"] == "create_user"
    assert payload["request"]["detail_level"] == "auto"
    assert payload["request"]["returned_detail_level"] == "minimal"
    assert payload["budgets"]["total_estimated_tokens"] >= 1
    assert payload["token_usage"]["estimator"] == "chars/4 proxy"
    assert payload["token_usage"]["used_tokens"] >= 1
    assert payload["token_usage"]["baseline_tokens"] >= 1
    assert payload["token_usage"]["saved_tokens"] >= 0
    assert payload["sufficiency"]["sufficient"] is True
    assert payload["sufficiency"]["verdict"] == "sufficient"
    assert payload["sufficiency"]["applicable_metrics"]
    assert "semantic_overlap_relaxed" in payload["sufficiency"]["thresholds"]
    assert payload["sufficiency"]["failure_reasons"] == []
    assert payload["sufficiency"]["recovery"] == []
    assert payload["next_actions"]
    assert any(action["action"] == "expand_context" for action in payload["next_actions"])
    assert payload["warnings"] == []

    assert payload["symbols"][0]["id"] == "symbol::service.py::function::create_user"

    assert payload["target"]["resolution"] == "resolved"
    assert payload["target"]["confidence"] == 1.0
    assert payload["target"]["candidates"] == []

    for node in payload["symbols"]:
        for key in expected["canonical_node_fields"]:
            assert key in node
        assert node["language"] == "python"
        assert set(node["reason"]).issubset(VALID_REASONS)
        assert node["reason_details"]
        assert {d["code"] for d in node["reason_details"]}.issubset(VALID_REASONS)
        assert all(
            "confidence_tier" in d and "score_contribution" in d for d in node["reason_details"]
        )
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

    assert payload["schema_version"] == "csegraph-context-v3"
    assert any("explanation" in node for node in payload["symbols"])
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

    assert payload["request"]["detail_level"] == "standard"
    assert payload["request"]["returned_detail_level"] == "standard"
    assert any("source_text" in node for node in payload["symbols"])
    assert any(
        node["id"] == "symbol::service.py::function::create_user"
        and "def create_user(name: str) -> dict:" in node.get("source_text", "")
        for node in payload["symbols"]
    )


def test_broad_prompt_with_low_confidence_inferred_target_is_not_sufficient(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task=(
            "Plan verified MCP integration support for Claude Code, Cursor, Kiro, "
            "Antigravity, Gemini CLI, GitHub Copilot, Codex, and VS Code."
        ),
        profile="small",
    )
    payload = to_dict(context)

    assert payload["target"]["resolution"] == "inferred"
    assert payload["target"]["confidence"] < 0.55
    assert payload["sufficiency"]["sufficient"] is False
    assert payload["sufficiency"]["verdict"] == "not_sufficient"
    assert any(
        reason.get("metric") == "target_confidence"
        for reason in payload["sufficiency"]["failure_reasons"]
    )


def test_broad_architecture_prompt_suggests_architecture_recovery(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="What should we improve in the architecture and retrieval roadmap?",
        profile="small",
    )
    payload = to_dict(context)

    assert payload["target"]["resolution"] == "inferred"
    assert payload["target"]["confidence"] < 0.55
    actions = {
        recovery.get("action"): recovery for recovery in payload["sufficiency"]["recovery"]
    }
    assert "try_architecture_context" in actions
    assert actions["try_architecture_context"]["tool"] == "csegraph_context"
    assert actions["try_architecture_context"]["suggested_targets"]


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

    assert payload["request"]["detail_level"] == "full"
    assert payload["request"]["returned_detail_level"] == "full"
    assert all("source_text" in node for node in payload["symbols"])
    assert any("explanation" in node for node in payload["symbols"])


def test_context_file_nodes_do_not_materialize_whole_file_source(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    repo.mkdir(parents=True, exist_ok=True)
    app_source = "\n\n".join(f"def f{index}():\n    return {index}" for index in range(12))
    (repo / "app.py").write_text(f"{app_source}\n", encoding="utf-8")
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="inspect app.py f0 f11",
        target="app.py",
        profile="small",
        detail_level="full",
    )
    payload = to_dict(context)
    by_id = {node["id"]: node for node in payload["symbols"]}

    assert "file::app.py" not in by_id

    f11 = by_id["symbol::app.py::function::f11"]
    assert f11["line_range"] == [34, 35]
    assert f11["source_text"] == "def f11():\n    return 11\n"
    assert "def f0" not in f11["source_text"]


def test_context_returns_symbol_neighborhood_for_auth_flow(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_auth_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="change authenticate_user password token behavior used by login middleware",
        target="authenticate_user",
        profile="small",
        detail_level="standard",
        include_source="always",
    )
    payload = to_dict(context)

    by_name = {symbol["name"]: symbol for symbol in payload["symbols"]}
    assert {
        "authenticate_user",
        "verify_password",
        "issue_token",
        "login",
        "require_auth",
    }.issubset(by_name)
    assert "rotate_password" not in by_name
    assert "revoke_token" not in by_name
    assert all(not symbol["id"].startswith("file::") for symbol in payload["symbols"])
    assert "nodes" not in payload

    auth_source = by_name["authenticate_user"]["source_text"]
    assert "def authenticate_user" in auth_source
    assert "from passwords import verify_password" not in auth_source
    assert "def audit_login" not in auth_source
    assert "irrelevant_helper_" not in "\n".join(
        symbol.get("source_text", "") for symbol in payload["symbols"]
    )

    relationships = {
        (relationship["source"], relationship["relation"], relationship["target"])
        for relationship in payload["relationships"]
    }
    authenticate = "symbol::auth.py::function::authenticate_user"
    assert (
        authenticate,
        "calls",
        "symbol::passwords.py::function::verify_password",
    ) in relationships
    assert (authenticate, "calls", "symbol::sessions.py::function::issue_token") in relationships
    assert ("symbol::routes.py::function::login", "calls", authenticate) in relationships
    assert ("symbol::middleware.py::function::require_auth", "calls", authenticate) in relationships
    assert ("file::auth.py", "imports", "file::passwords.py") in relationships
    assert ("file::auth.py", "imports", "file::sessions.py") in relationships
    extracted_call = next(
        relationship
        for relationship in payload["relationships"]
        if relationship["relation"] == "calls"
    )
    assert extracted_call["occurrences"]
    assert {
        "path",
        "line_range",
        "enclosing_symbol_id",
        "name",
        "kind",
        "snippet",
        "metadata",
    }.issuperset(extracted_call["occurrences"][0])
    assert extracted_call["occurrences"][0]["kind"] == "calls"
    assert "authenticate_user(" in "\n".join(
        occurrence.get("snippet", "")
        for relationship in payload["relationships"]
        for occurrence in relationship.get("occurrences", [])
    )
    assert "confidence" not in extracted_call
    assert "confidence_tier" not in extracted_call
    assert "source_path" not in extracted_call
    assert "target_path" not in extracted_call

    preludes_by_path = {prelude["path"]: prelude for prelude in payload["import_preludes"]}
    assert preludes_by_path["auth.py"]["text"] == (
        "from passwords import verify_password\nfrom sessions import issue_token"
    )
    assert preludes_by_path["routes.py"]["text"] == "from auth import authenticate_user"
    assert preludes_by_path["middleware.py"]["text"] == "from auth import authenticate_user"
    assert all("def " not in prelude["text"] for prelude in payload["import_preludes"])


def test_occurrence_cap_prioritizes_target_calls_over_imports(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_auth_repo(repo)
    IndexService(db_path).index(repo, profile="small")
    monkeypatch.setitem(context_module.OCCURRENCE_LIMIT_BY_PROFILE, "small", 1)

    context = ContextService(db_path).build_context(
        task="change authenticate_user password token behavior",
        target="authenticate_user",
        profile="small",
        detail_level="standard",
        include_source="always",
    )
    payload = to_dict(context)

    relationships_with_occurrences = [
        relationship for relationship in payload["relationships"] if relationship.get("occurrences")
    ]
    occurrences = [
        (relationship, occurrence)
        for relationship in relationships_with_occurrences
        for occurrence in relationship["occurrences"]
    ]
    authenticate = "symbol::auth.py::function::authenticate_user"
    assert len(occurrences) == 1

    relationship, occurrence = occurrences[0]
    assert relationship["source"] == authenticate
    assert relationship["relation"] == "calls"
    assert occurrence["enclosing_symbol_id"] == authenticate
    assert occurrence["kind"] == "calls"
    assert any(
        call in occurrence.get("snippet", "")
        for call in ("load_user(", "verify_password(", "issue_token(")
    )


def test_relationship_occurrence_dedup_preserves_first_seen_order(tmp_path, monkeypatch):
    relationship = ContextRelationship(
        source="symbol::auth.py::function::authenticate_user",
        target="symbol::passwords.py::function::verify_password",
        relation="calls",
    )
    duplicate_row = {
        "path": "auth.py",
        "start_line": 6,
        "end_line": 6,
        "source": "verify_password(password, user['password_hash'])",
        "metadata": "{}",
        "enclosing_symbol_id": "symbol::auth.py::function::authenticate_user",
        "name": "verify_password",
        "kind": "calls",
    }
    second_row = {
        **duplicate_row,
        "start_line": 8,
        "end_line": 8,
        "source": "return issue_token(user['id'])",
        "name": "issue_token",
    }

    class FakeIndex:
        def metadata(self):
            return {"root_dir": str(tmp_path)}

    monkeypatch.setattr(
        context_module,
        "_relationship_reference_rows",
        lambda _index, _relationship: [duplicate_row, dict(duplicate_row), second_row],
    )
    monkeypatch.setattr(context_module, "OCCURRENCE_PER_RELATIONSHIP_LIMIT", 3)

    context_module._attach_symbol_reference_occurrences(
        FakeIndex(),
        [relationship],
        include_snippet=True,
        total_limit=10,
    )
    context_module._dedupe_relationship_occurrences([relationship])

    assert [occurrence.name for occurrence in relationship.occurrences] == [
        "verify_password",
        "issue_token",
    ]
    assert [occurrence.line_range for occurrence in relationship.occurrences] == [[6, 6], [8, 8]]


def test_include_source_never_still_returns_relationships_and_import_preludes(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_auth_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="change authenticate_user password token behavior",
        target="authenticate_user",
        profile="small",
        detail_level="standard",
        include_source="never",
    )
    payload = to_dict(context)

    assert all("source_text" not in symbol for symbol in payload["symbols"])
    assert {symbol.get("source_omitted_reason") for symbol in payload["symbols"]} == {
        "source_policy_never"
    }
    assert any(relationship["relation"] == "calls" for relationship in payload["relationships"])
    assert any(relationship.get("occurrences") for relationship in payload["relationships"])
    assert all(
        "snippet" not in occurrence
        for relationship in payload["relationships"]
        for occurrence in relationship.get("occurrences", [])
    )
    preludes_by_path = {prelude["path"]: prelude for prelude in payload["import_preludes"]}
    assert preludes_by_path["auth.py"]["text"] == (
        "from passwords import verify_password\nfrom sessions import issue_token"
    )


def test_import_preludes_filter_irrelevant_imports(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_auth_repo(repo)
    (repo / "audit.py").write_text(
        "def write_audit(event: str) -> None:\n    return None\n",
        encoding="utf-8",
    )
    (repo / "auth.py").write_text(
        "from audit import write_audit\n"
        "from passwords import verify_password\n"
        "from sessions import issue_token\n\n"
        "def authenticate_user(username: str, password: str) -> str | None:\n"
        "    user = load_user(username)\n"
        "    if not verify_password(password, user['password_hash']):\n"
        "        return None\n"
        "    return issue_token(user['id'])\n\n"
        "def load_user(username: str) -> dict:\n"
        "    return {'id': username, 'password_hash': f'hash:{username}'}\n",
        encoding="utf-8",
    )
    IndexService(db_path).index(repo, profile="small")

    payload = to_dict(
        ContextService(db_path).build_context(
            task="change authenticate_user password token behavior",
            target="authenticate_user",
            profile="small",
            detail_level="standard",
        )
    )

    auth_prelude = next(
        prelude for prelude in payload["import_preludes"] if prelude["path"] == "auth.py"
    )
    assert auth_prelude["text"] == (
        "from passwords import verify_password\nfrom sessions import issue_token"
    )


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

    assert payload["request"]["detail_level"] == "auto"
    assert payload["request"]["returned_detail_level"] == "standard"
    assert payload["sufficiency"]["sufficient"] is False
    assert payload["sufficiency"]["failure_reasons"]
    assert payload["sufficiency"]["recovery"]
    assert any("source_text" in node for node in payload["symbols"])


def test_context_auto_uses_minimal_response_before_promoting(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "csegraph.json").write_text(
        json.dumps({"semantic_threshold_relaxed": 0.2}),
        encoding="utf-8",
    )
    (repo / "helpers.py").write_text(
        "def clean_name(value: str) -> str:\n    return value.strip().lower()\n",
        encoding="utf-8",
    )
    (repo / "service.py").write_text(
        "from helpers import clean_name\n\n"
        "def create_user(name: str) -> dict:\n"
        "    normalized = clean_name(name)\n"
        "    return {'clean_name': normalized}\n",
        encoding="utf-8",
    )
    for index in range(18):
        (repo / f"noise_{index}.py").write_text(
            f"def unrelated_{index}():\n"
            f"    return 'omega_{index} zeta_{index} theta_{index} lambda_{index}'\n",
            encoding="utf-8",
        )
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="create_user clean_name",
        target="create_user",
        profile="small",
        detail_level="auto",
    )
    payload = to_dict(context)

    assert payload["request"]["detail_level"] == "auto"
    assert payload["request"]["returned_detail_level"] == "minimal"
    assert payload["sufficiency"]["sufficient"] is True
    assert len(payload["symbols"]) <= 5
    assert [node["name"] for node in payload["symbols"]] == ["create_user", "clean_name"]


def test_standard_context_uses_profile_bounded_dependency_sufficiency(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    repo.mkdir(parents=True, exist_ok=True)
    helper_defs = "\n\n".join(
        f"def step_{index}(value: str) -> str:\n    return value + '-{index}'"
        for index in range(20)
    )
    calls = "\n".join(f"    value = step_{index}(value)" for index in range(20))
    (repo / "workflow.py").write_text(
        f"{helper_defs}\n\n"
        "def orchestrate_context(value: str) -> str:\n"
        f"{calls}\n"
        "    return value\n",
        encoding="utf-8",
    )
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="change orchestrate_context payload flow",
        target="orchestrate_context",
        profile="small",
        detail_level="auto",
    )
    payload = to_dict(context)

    assert payload["request"]["returned_detail_level"] == "standard"
    assert payload["sufficiency"]["sufficient"] is True
    assert payload["sufficiency"]["metrics"]["dependency_completeness"] == 1.0
    assert payload["sufficiency"]["thresholds"]["dependency_budget"] == 8.0
    assert payload["warnings"] == []
    assert len(payload["symbols"]) <= 16
    assert sum(1 for symbol in payload["symbols"] if symbol["name"].startswith("step_")) >= 8


def test_context_auto_upgrades_when_task_has_no_semantic_overlap(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_repo(repo)
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="perform billing reconciliation",
        target="create_user",
        profile="small",
        detail_level="auto",
    )
    payload = to_dict(context)

    assert payload["request"]["returned_detail_level"] == "standard"
    assert payload["sufficiency"]["sufficient"] is False
    assert payload["sufficiency"]["metrics"]["semantic_overlap"] == 0.0
    assert payload["sufficiency"]["thresholds"]["semantic_overlap_relaxed"] == 0.05
    assert any("source_text" in node for node in payload["symbols"])


def test_context_semantic_relaxed_zero_preserves_legacy_minimal_behavior(tmp_path):
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.db"
    _write_repo(repo)
    (repo / "csegraph.json").write_text(
        json.dumps({"semantic_threshold_relaxed": 0.0}),
        encoding="utf-8",
    )
    IndexService(db_path).index(repo, profile="small")

    context = ContextService(db_path).build_context(
        task="perform billing reconciliation",
        target="create_user",
        profile="small",
        detail_level="auto",
    )
    payload = to_dict(context)

    assert payload["request"]["returned_detail_level"] == "minimal"
    assert payload["sufficiency"]["sufficient"] is True
    assert payload["sufficiency"]["thresholds"]["semantic_overlap_relaxed"] == 0.0


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

    assert payload["request"]["detail_level"] == "minimal"
    assert payload["request"]["returned_detail_level"] == "minimal"
    assert all("source_text" not in node for node in payload["symbols"])
    assert any("explanation" in node for node in payload["symbols"])


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
    source_free = ContextService(db_path).build_context(
        task="long_documented",
        target="long_documented",
        profile="small",
        detail_level="standard",
        include_source="never",
    )
    source_free_payload = to_dict(source_free)

    minimal_target = next(n for n in minimal_payload["symbols"] if n["id"] == node_id)
    standard_target = next(n for n in standard_payload["symbols"] if n["id"] == node_id)
    source_free_target = next(n for n in source_free_payload["symbols"] if n["id"] == node_id)

    # Minimal summary should be truncated and end with "..."
    assert len(minimal_target["summary"]) <= 240
    assert minimal_target["summary"].endswith("...")

    # Standard summary should be full and not truncated
    assert standard_target["summary"] == long_summary
    assert len(source_free_target["summary"]) <= 240
    assert source_free_target["summary"].endswith("...")


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
    assert payload["request"]["detail_level"] == "auto"
    assert payload["request"]["returned_detail_level"] == "standard"
    assert payload["sufficiency"]["sufficient"] is False
    assert payload["warnings"] == ["Context sufficiency thresholds were not met."]
