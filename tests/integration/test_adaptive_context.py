from __future__ import annotations

from pathlib import Path

import pytest

from csegraph import ContextRequest, ContextService, ContextStatus, IndexService, to_dict
from csegraph._core.retrieval.adaptive_caps import derive_retrieval_caps
from csegraph._core.retrieval.adaptive_ranking import _select_context_rows
from csegraph._core.retrieval.token_budget import count_payload_tokens


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "helpers.py").write_text(
        "def fmt(name: str) -> str:\n    return f'hi {name}'\n",
        encoding="utf-8",
    )
    (repo / "app.py").write_text(
        "from helpers import fmt\n\ndef greet(name: str) -> str:\n    return fmt(name)\n",
        encoding="utf-8",
    )
    db = str(repo / ".csegraph" / "index.db")
    IndexService(db).index(repo)
    return repo, db


def test_compact_context_is_the_only_response_contract(tmp_path: Path):
    repo, db = _repo(tmp_path)

    result = ContextService(db).retrieve(
        ContextRequest(repo=str(repo), task="Explain greet", target="greet")
    )
    payload = to_dict(result)

    assert result.status is ContextStatus.READY
    assert set(payload) == {"schema_version", "status", "slices"}
    assert payload["schema_version"] == "csegraph-context-v5"
    assert set(payload["slices"][0]) == {"path", "lines", "symbol", "role", "code"}
    assert "def greet" in payload["slices"][0]["code"]


def test_diagnostics_are_opt_in_and_share_the_token_budget(tmp_path: Path):
    repo, db = _repo(tmp_path)
    request = ContextRequest(
        repo=str(repo),
        task="Explain greet",
        target="greet",
        token_budget=800,
        diagnostic=True,
    )

    payload = to_dict(ContextService(db).retrieve(request))

    assert "diagnostics" in payload
    assert payload["diagnostics"]["target"]["name"] == "greet"
    assert count_payload_tokens(payload, "o200k_base") <= request.token_budget


def test_diagnostics_do_not_reject_content_that_fits_the_budget(tmp_path: Path):
    repo, db = _repo(tmp_path)
    payload = to_dict(
        ContextService(db).retrieve(
            ContextRequest(
                repo=str(repo),
                task="Trace callers and tests for greet",
                target="greet",
                task_kind="review",
                token_budget=800,
                diagnostic=True,
            )
        )
    )

    assert payload["status"] == "ready"
    assert payload["slices"][0]["symbol"] == "greet"


def test_retrieval_caps_scale_with_task_shape_and_repository_size():
    definition = derive_retrieval_caps(
        ContextRequest(task="Explain greet", target="greet", token_budget=800),
        intent="understand",
        plan_mode="lexical",
        metadata={"file_count": "8", "symbol_count": "24"},
        exact_target=True,
    )
    impact = derive_retrieval_caps(
        ContextRequest(
            task="Trace callers, tests, and dependencies for greet",
            task_kind="review",
            token_budget=2400,
        ),
        intent="review",
        plan_mode="impact",
        metadata={"file_count": "2400", "symbol_count": "120000"},
        exact_target=False,
    )

    assert definition.slice_limit == 1
    assert impact.slice_limit >= 5
    assert impact.slice_limit > definition.slice_limit
    assert impact.candidate_limit > definition.candidate_limit
    assert impact.relationship_limit > definition.relationship_limit
    assert impact.candidate_limit <= impact.hard_candidate_limit
    assert impact.slice_limit <= impact.hard_slice_limit


def test_diagnostics_expose_the_caps_used_for_the_request(tmp_path: Path):
    repo, db = _repo(tmp_path)
    payload = to_dict(
        ContextService(db).retrieve(
            ContextRequest(
                repo=str(repo),
                task="Trace callers and tests for greet",
                target="greet",
                task_kind="review",
                token_budget=1600,
                diagnostic=True,
            )
        )
    )

    caps = payload["diagnostics"]["caps"]
    assert caps["slice_limit"] >= 2
    assert caps["candidate_limit"] >= 1
    assert len(payload.get("slices", [])) <= caps["slice_limit"]


def test_context_selection_prioritizes_relationships_requested_by_the_task():
    rows = [
        {"id": "target", "path": "app.py", "start_line": 1, "_rank_position": 0},
        {"id": "dependency", "path": "deps.py", "start_line": 2, "_rank_position": 1},
        {"id": "caller", "path": "caller.py", "start_line": 3, "_rank_position": 2},
        {"id": "test", "path": "tests/test_app.py", "start_line": 4, "_rank_position": 3},
    ]
    roles = {"target": "target", "dependency": "dependency", "caller": "caller", "test": "test"}

    selected = _select_context_rows(
        rows,
        target_id="target",
        roles=roles,
        intent="review",
        task="Find callers and tests for the target",
        limit=4,
    )

    assert [row["id"] for row in selected] == ["target", "caller", "test"]


def test_context_selection_keeps_multiple_requested_dependencies():
    rows = [
        {"id": "target", "path": "app.py", "start_line": 1, "_rank_position": 0},
        {"id": "dependency-one", "path": "one.py", "start_line": 2, "_rank_position": 1},
        {"id": "dependency-two", "path": "two.py", "start_line": 3, "_rank_position": 2},
        {"id": "test", "path": "tests/test_app.py", "start_line": 4, "_rank_position": 3},
    ]
    roles = {
        "target": "target",
        "dependency-one": "dependency",
        "dependency-two": "dependency",
        "test": "test",
    }

    selected = _select_context_rows(
        rows,
        target_id="target",
        roles=roles,
        intent="edit",
        task="Inspect dependencies and tests for the target",
        limit=4,
    )

    assert [row["id"] for row in selected] == [
        "target",
        "dependency-one",
        "dependency-two",
        "test",
    ]


def test_source_mode_never_omits_source_material(tmp_path: Path):
    repo, db = _repo(tmp_path)

    payload = to_dict(
        ContextService(db).retrieve(
            ContextRequest(
                repo=str(repo),
                task="Explain greet",
                target="greet",
                source_mode="never",
            )
        )
    )

    assert payload["status"] == "ready"
    assert all(slice_["code"] == "" for slice_ in payload["slices"])


def test_removed_request_options_are_rejected_by_the_type(tmp_path: Path):
    repo, _ = _repo(tmp_path)

    with pytest.raises(TypeError):
        ContextRequest(  # type: ignore[call-arg]
            repo=str(repo),
            task="Explain greet",
            response_mode="legacy-v3",
        )


def test_ambiguous_response_has_standard_continuation_shape(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "one.py").write_text("def shared():\n    return 1\n", encoding="utf-8")
    (repo / "two.py").write_text("def shared():\n    return 2\n", encoding="utf-8")
    db = str(repo / ".csegraph" / "index.db")
    IndexService(db).index(repo)

    payload = to_dict(
        ContextService(db).retrieve(
            ContextRequest(repo=str(repo), task="Explain shared", target="shared")
        )
    )

    assert payload["status"] == "ambiguous"
    assert len(payload["candidates"]) == 2
    assert set(payload["next"]) <= {"tool", "arguments", "reason"}
    assert "args" not in payload["next"]


def test_tiny_repo_exact_target_skips_full_candidate_discovery(
    tmp_path: Path,
    monkeypatch,
):
    repo, db = _repo(tmp_path)

    def explode(*args, **kwargs):
        raise AssertionError("tiny repos should not need full candidate discovery")

    monkeypatch.setattr("csegraph._core.retrieval.adaptive._discover_candidates", explode)

    payload = to_dict(
        ContextService(db).retrieve(
            ContextRequest(repo=str(repo), task="Explain greet", target="greet")
        )
    )

    assert payload["status"] == "ready"
    assert payload["slices"][0]["symbol"] == "greet"
