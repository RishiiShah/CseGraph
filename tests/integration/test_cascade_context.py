from __future__ import annotations

import concurrent.futures
import threading
from pathlib import Path

import pytest

from csegraph import ContextRequest, ContextService, IndexService, to_dict
from csegraph._core.index.services import RefreshService
from csegraph._core.retrieval.freshness.coordinator import FreshnessCoordinator
from csegraph._core.retrieval.token_budget import count_payload_tokens
from csegraph._core.server.app import _handle_tool


def _chain_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "c.py").write_text("def c():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text(
        "from c import c\n\ndef b():\n    return c()\n",
        encoding="utf-8",
    )
    (repo / "a.py").write_text(
        "from b import b\n\ndef a():\n    return b()\n",
        encoding="utf-8",
    )
    db = str(repo / ".csegraph" / "index.db")
    IndexService(db).index(repo)
    return repo, db


def test_context_reaches_transitive_callers(tmp_path: Path):
    repo, db = _chain_repo(tmp_path)

    payload = to_dict(
        ContextService(db).retrieve(
            ContextRequest(
                repo=str(repo),
                task="Fix cascading change in c",
                target="c",
                task_kind="edit",
                token_budget=4000,
            )
        )
    )

    assert payload["status"] == "ready"
    selected = {(item["symbol"], item["role"]) for item in payload["slices"]}
    assert ("c", "target") in selected
    assert ("b", "caller") in selected
    assert ("a", "caller") in selected


@pytest.mark.parametrize("scope", ["one-hop", "one hop", "immediate"])
def test_explicit_one_hop_request_keeps_direct_scope(tmp_path: Path, scope):
    repo, db = _chain_repo(tmp_path)
    result = ContextService(db).retrieve(
        ContextRequest(
            repo=str(repo), task=f"Inspect {scope} callers of c", target="c", task_kind="review"
        )
    )
    assert {item.symbol for item in result.slices} == {"c", "b"}


def test_refresh_pushes_changed_and_cascading_context(tmp_path: Path):
    repo, db = _chain_repo(tmp_path)
    changed = repo / "c.py"
    changed.write_text("def c():\n    return 2\n", encoding="utf-8")

    result = RefreshService(db).refresh(changed_paths=[changed])
    payload = to_dict(result)

    assert result.dependents_expanded == 2
    assert set(result.impacted_files) == {"a.py", "b.py", "c.py"}
    assert payload["context"]["schema_version"] == "csegraph-context-v5"
    roles = {(item["symbol"], item["role"]) for item in payload["context"]["slices"]}
    assert ("c", "changed") in roles
    assert ("b", "caller") in roles
    assert ("a", "cascade") in roles


def test_deleted_file_pushes_surviving_dependents(tmp_path: Path):
    repo, db = _chain_repo(tmp_path)
    (repo / "c.py").unlink()

    payload = _handle_tool("csegraph_refresh", {"repo": str(repo)})

    assert payload["deleted_files"] == ["c.py"]
    assert set(payload["impacted_files"]) == {"a.py", "b.py", "c.py"}
    assert payload["context"]["schema_version"] == "csegraph-context-v5"
    assert {item["symbol"] for item in payload["context"]["slices"]} == {"a", "b"}
    assert all(item["path"] != "c.py" for item in payload["context"]["slices"])


@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize("replacement", ["", "def renamed():\n    return 2\n\n"])
def test_removed_symbol_pushes_dependents_from_surviving_file(
    tmp_path: Path, monkeypatch, reuse, replacement
):
    repo, db = _chain_repo(tmp_path)
    source = repo / "c.py"
    survivor = "def unrelated():\n    return 1\n"
    source.write_text(source.read_text() + "\n" + survivor)
    IndexService(db).index(repo)
    source.write_text(replacement + survivor)
    coordinator = FreshnessCoordinator(db)
    if reuse:
        acquire = coordinator._acquire_lease

        def publish_then_acquire(root, owner):
            RefreshService(db).refresh(include_context=False)
            RefreshService(db).refresh(include_context=False)
            return acquire(root, owner)

        monkeypatch.setattr(coordinator, "_acquire_lease", publish_then_acquire)

    result = coordinator.explicit_refresh(repo, token_budget=4000)

    assert result.deleted_files == []
    assert set(result.impacted_files) == {"a.py", "b.py", "c.py"}
    assert result.context is not None
    assert {item.symbol for item in result.context.slices} >= {"unrelated", "a", "b"}
    assert ("Reused" in " ".join(result.warnings)) == reuse


@pytest.mark.parametrize("changed_name", ["decorate", "model"])
def test_refresh_preserves_decorator_relationship_direction(tmp_path: Path, changed_name):
    repo = tmp_path / "repo"
    repo.mkdir()
    decorator = repo / "decorator.py"
    decorated = repo / "model.py"
    decorator.write_text("def decorate(value):\n    return value\n")
    decorated.write_text("from decorator import decorate\n\n@decorate\ndef model():\n    pass\n")
    db = str(repo / ".csegraph" / "index.db")
    IndexService(db).index(repo)
    source = decorator if changed_name == "decorate" else decorated
    source.write_text(source.read_text() + "\n# changed\n")

    result = RefreshService(db).refresh()

    assert result.context is not None
    roles = {item.symbol: item.role for item in result.context.slices}
    assert roles[changed_name] == "changed"
    assert roles["model" if changed_name == "decorate" else "decorate"] == (
        "caller" if changed_name == "decorate" else "dependency"
    )


@pytest.mark.parametrize("delete", [False, True])
def test_concurrent_explicit_refresh_reuses_context(tmp_path: Path, monkeypatch, delete):
    repo, db = _chain_repo(tmp_path)
    changed = repo / "c.py"
    if delete:
        changed.unlink()
    else:
        changed.write_text("def c():\n    return 3\n", encoding="utf-8")
    writer_started = threading.Event()
    reader_waiting = threading.Event()
    release_writer = threading.Event()
    real_refresh = RefreshService.refresh
    real_wait = FreshnessCoordinator._wait_for_active_refresh

    def blocked_refresh(service, *args, **kwargs):
        writer_started.set()
        assert release_writer.wait(timeout=5)
        return real_refresh(service, *args, **kwargs)

    def recording_wait(coordinator, *args, **kwargs):
        reader_waiting.set()
        return real_wait(coordinator, *args, **kwargs)

    monkeypatch.setattr(RefreshService, "refresh", blocked_refresh)
    monkeypatch.setattr(FreshnessCoordinator, "_wait_for_active_refresh", recording_wait)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(FreshnessCoordinator(db).explicit_refresh, repo)
        try:
            assert writer_started.wait(timeout=5)
            reader = executor.submit(
                FreshnessCoordinator(db).explicit_refresh,
                repo,
                task="Inspect cascading callers",
                token_budget=512,
            )
            assert reader_waiting.wait(timeout=5)
        finally:
            release_writer.set()
        published = writer.result(timeout=5)
        reused = reader.result(timeout=5)

    assert "Reused" in reused.warnings[0]
    assert reused.impacted_files == published.impacted_files
    assert reused.deleted_files == published.deleted_files
    assert reused.context is not None
    assert {item.symbol for item in reused.context.slices} == (
        {"a", "b"} if delete else {"a", "b", "c"}
    )
    assert count_payload_tokens(to_dict(reused.context), "o200k_base") <= 512


def test_refresh_task_prioritizes_context_and_respects_budget(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "app.py"
    source.write_text("def alpha():\n    return 1\n\ndef omega():\n    return 2\n")
    _handle_tool("csegraph_index", {"repo": str(repo)})
    source.write_text("def alpha():\n    return 3\n\ndef omega():\n    return 4\n")

    payload = _handle_tool(
        "csegraph_refresh",
        {"repo": str(repo), "task": "Inspect omega", "token_budget": 256},
    )

    assert payload["changed_files"] == ["app.py"]
    context = payload["context"]
    assert set(context) == {"schema_version", "status", "slices"}
    assert context["status"] == "ready"
    assert context["slices"][0]["symbol"] == "omega"
    assert set(context["slices"][0]) == {"path", "lines", "symbol", "role", "code"}
    assert context["slices"][0]["code"] == "def omega():\n    return 4\n"
    assert count_payload_tokens(context, "o200k_base") <= 256


def test_oversized_changed_symbol_does_not_hide_smaller_context(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "app.py"
    large = "def large():\n" + "    value = 'some large source content'\n" * 200
    source.write_text(large + "\ndef small():\n    return 1\n")
    db = str(repo / ".csegraph" / "index.db")
    IndexService(db).index(repo)
    source.write_text(large + "\ndef small():\n    return 2\n")

    result = RefreshService(db).refresh(context_token_budget=256)

    assert result.context is not None
    assert "small" in {item.symbol for item in result.context.slices}
    assert count_payload_tokens(to_dict(result.context), "o200k_base") <= 256


def test_refresh_cascades_through_file_imports_without_symbols(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "c.py").write_text("VALUE = 1\n")
    (repo / "b.py").write_text("import c\n")
    (repo / "a.py").write_text("import b\n")
    db = str(repo / ".csegraph" / "index.db")
    IndexService(db).index(repo)
    (repo / "c.py").write_text("VALUE = 2\n")

    result = RefreshService(db).refresh()

    assert set(result.impacted_files) == {"a.py", "b.py", "c.py"}
    assert result.dependents_expanded == 2


@pytest.mark.parametrize("reuse", [False, True])
def test_import_only_dependents_push_surviving_symbols(tmp_path: Path, monkeypatch, reuse):
    repo, db = _chain_repo(tmp_path)
    (repo / "b.py").write_text("import c\n\ndef b():\n    return 1\n")
    (repo / "a.py").write_text("import b\n\ndef a():\n    return 1\n")
    IndexService(db).index(repo)
    (repo / "c.py").write_text("def c():\n    return 2\n")
    coordinator = FreshnessCoordinator(db)
    if reuse:
        acquire = coordinator._acquire_lease

        def publish_then_acquire(root, owner):
            RefreshService(db).refresh(include_context=False)
            return acquire(root, owner)

        monkeypatch.setattr(coordinator, "_acquire_lease", publish_then_acquire)

    result = coordinator.explicit_refresh(repo, token_budget=4000)

    assert set(result.impacted_files) == {"a.py", "b.py", "c.py"}
    assert result.context is not None
    assert {item.symbol: item.role for item in result.context.slices} == {
        "c": "changed",
        "b": "impacted",
        "a": "impacted",
    }
    assert ("Reused" in " ".join(result.warnings)) == reuse


@pytest.mark.parametrize("dependent_count", [2, 3])
def test_dependent_cap_reports_only_omitted_dependents(tmp_path: Path, dependent_count):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "c.py").write_text("def c():\n    return 1\n")
    for number in range(dependent_count):
        (repo / f"caller_{number}.py").write_text(
            f"from c import c\n\ndef caller_{number}():\n    return c()\n"
        )
    db = str(repo / ".csegraph" / "index.db")
    IndexService(db).index(repo)
    (repo / "c.py").write_text("def c():\n    return 2\n")

    result = RefreshService(db).refresh(dependents_limit=2)

    assert result.dependents_expanded == 2
    assert result.dependents_cap_hit == (dependent_count > 2)


def test_deletion_context_survives_unrelated_changed_symbols(tmp_path: Path):
    repo, db = _chain_repo(tmp_path)
    other = repo / "other.py"
    other.write_text("def unrelated():\n    return 1\n")
    IndexService(db).index(repo)
    (repo / "c.py").unlink()
    other.write_text("def unrelated():\n    return 2\n")

    result = RefreshService(db).refresh()

    assert result.context is not None
    assert {item.symbol for item in result.context.slices} >= {"unrelated", "b"}


def test_explicit_refresh_reuses_revision_published_before_lease_acquisition(
    tmp_path: Path, monkeypatch
):
    repo, db = _chain_repo(tmp_path)
    (repo / "c.py").write_text("def c():\n    return 4\n")
    coordinator = FreshnessCoordinator(db)
    acquire = coordinator._acquire_lease

    def publish_then_acquire(root, owner):
        RefreshService(db).refresh(include_context=False)
        RefreshService(db).refresh(include_context=False)
        return acquire(root, owner)

    monkeypatch.setattr(coordinator, "_acquire_lease", publish_then_acquire)

    result = coordinator.explicit_refresh(repo)

    assert result.context is not None
    assert {item.symbol for item in result.context.slices} == {"a", "b", "c"}
    assert "Reused" in result.warnings[0]


def test_refresh_reports_insufficient_budget_when_all_source_is_oversized(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "app.py"
    source.write_text("def large():\n    return 1\n")
    db = str(repo / ".csegraph" / "index.db")
    IndexService(db).index(repo)
    source.write_text("def large():\n" + "    value = 'large source content'\n" * 200)

    result = RefreshService(db).refresh(context_token_budget=256)

    assert result.context is not None
    payload = to_dict(result.context)
    assert payload["status"] == "insufficient"
    assert payload["slices"] == []
    assert payload["missing"][0]["path"] == "app.py"
    assert payload["missing"][0]["required_budget"] > 256
    assert count_payload_tokens(payload, "o200k_base") <= 256


@pytest.mark.parametrize("budget", [0, 255, 16385, True])
def test_refresh_rejects_invalid_context_budget_before_writing(tmp_path: Path, budget):
    db = tmp_path / "index.db"
    with pytest.raises((ValueError, TypeError), match="token_budget"):
        RefreshService(db).refresh(context_token_budget=budget)
    with pytest.raises((ValueError, TypeError), match="token_budget"):
        FreshnessCoordinator(db).explicit_refresh(tmp_path, token_budget=budget)
    assert not db.exists()
