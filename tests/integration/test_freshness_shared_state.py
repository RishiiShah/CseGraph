from __future__ import annotations

import concurrent.futures
import multiprocessing
import os
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from csegraph._core.index.repository import ProjectIndex
from csegraph._core.index.services import IndexService
from csegraph._core.languages.base import sha256_text
from csegraph._core.retrieval import freshness as freshness_module
from csegraph._core.retrieval.freshness import (
    FreshnessCoordinator,
    _detect_changed_paths,
    _filesystem_changed_paths,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_concurrent_first_use_builds_one_index(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    db = repo / ".csegraph" / "index.db"
    real_index = IndexService.index
    calls = 0
    lock = threading.Lock()

    def delayed_index(service, *args, **kwargs):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.1)
        return real_index(service, *args, **kwargs)

    with patch.object(IndexService, "index", delayed_index):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _: FreshnessCoordinator(db).ensure_current(repo),
                    range(2),
                )
            )

    assert calls == 1
    assert {result.revision for result in results} == {1}
    assert {result.state for result in results} <= {"indexed", "current"}


def _git_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "CseGraph Tests")
    (repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "initial")
    db = tmp_path / "index.db"
    IndexService(db).index(repo)
    return repo, db


def test_non_git_freshness_reuses_snapshot_and_detects_file_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    app = repo / "app.py"
    app.write_text("def value():\n    return 1\n", encoding="utf-8")
    db = tmp_path / "index.db"
    IndexService(db).index(repo)
    monkeypatch.setattr(freshness_module, "TINY_FRESHNESS_FILE_LIMIT", 0)

    first = FreshnessCoordinator(db).ensure_current(repo)
    assert first.state == "current"

    with patch.object(
        freshness_module.registry,
        "iter_files",
        side_effect=AssertionError("unchanged snapshot should skip discovery"),
    ):
        second = FreshnessCoordinator(db).ensure_current(repo)
    assert second.state == "current"

    app.write_text("def value():\n    return 2\n", encoding="utf-8")
    with patch.object(
        freshness_module.registry,
        "iter_files",
        side_effect=AssertionError("changed indexed files should use the snapshot fast path"),
    ):
        changed = FreshnessCoordinator(db).ensure_current(repo)
    assert changed.state == "refreshed"


def _lease_process(
    db_path: str,
    repo_root: str,
    barrier: multiprocessing.synchronize.Barrier,
    queue: multiprocessing.queues.Queue,
) -> None:
    coordinator = FreshnessCoordinator(db_path, lease_seconds=0.4)
    owner = f"process-{os.getpid()}"
    barrier.wait(timeout=5)
    acquired = coordinator._acquire_lease(repo_root, owner)
    queue.put((owner, acquired))
    if acquired:
        # Exit without releasing: this also exercises crash/expiry recovery.
        time.sleep(0.1)


def _refresh_process(
    db_path: str,
    repo_root: str,
    barrier: multiprocessing.synchronize.Barrier,
    queue: multiprocessing.queues.Queue,
) -> None:
    barrier.wait(timeout=5)
    result = FreshnessCoordinator(
        db_path,
        refresh_timeout=5.0,
        lease_seconds=1.0,
        lease_renew_interval=0.1,
    ).ensure_current(repo_root)
    queue.put((result.state, result.status, result.revision))


def test_long_refresh_renews_lease_and_only_one_refresh_runs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, db = _git_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("def value():\n    return 2\n", encoding="utf-8")

    calls = 0
    calls_lock = threading.Lock()

    class SlowRefresh:
        def __init__(self, db_path: str | Path) -> None:
            self.db_path = db_path

        def refresh(self, **_kwargs):
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.35)
            index = ProjectIndex(self.db_path)
            try:
                revision = index.bump_index_revision()
            finally:
                index.close()
            return SimpleNamespace(
                changed_files=["app.py"],
                deleted_files=[],
                warnings=[],
                revision=revision,
            )

    monkeypatch.setattr(freshness_module, "RefreshService", SlowRefresh)
    coordinator_a = FreshnessCoordinator(
        db,
        refresh_timeout=1.0,
        lease_seconds=0.12,
        lease_renew_interval=0.025,
    )
    coordinator_b = FreshnessCoordinator(
        db,
        refresh_timeout=1.0,
        lease_seconds=0.12,
        lease_renew_interval=0.025,
    )
    barrier = threading.Barrier(2)
    results: list = []

    def run(coordinator: FreshnessCoordinator) -> None:
        barrier.wait(timeout=2)
        results.append(coordinator.ensure_current(repo))

    threads = [
        threading.Thread(target=run, args=(coordinator_a,)),
        threading.Thread(target=run, args=(coordinator_b,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert calls == 1
    assert len(results) == 2
    assert {result.state for result in results} == {"current", "refreshed"}
    assert {result.revision for result in results} == {2}


def test_sqlite_lease_is_atomic_across_processes_and_recovers_after_owner_exit(
    tmp_path: Path,
) -> None:
    repo, db = _git_repo(tmp_path)
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = [
        context.Process(
            target=_lease_process,
            args=(str(db), str(repo.resolve()), barrier, queue),
        )
        for _ in range(2)
    ]

    for process in processes:
        process.start()
    outcomes = [queue.get(timeout=5) for _ in processes]
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0

    assert sum(1 for _owner, acquired in outcomes if acquired) == 1
    time.sleep(0.45)
    recovery = FreshnessCoordinator(db, lease_seconds=0.4)
    assert recovery._acquire_lease(str(repo.resolve()), "recovery-owner")
    recovery._release_lease(str(repo.resolve()), "recovery-owner")


def test_concurrent_process_refreshes_once_and_share_the_new_revision(
    tmp_path: Path,
) -> None:
    repo, db = _git_repo(tmp_path)
    initial = ProjectIndex(db)
    try:
        initial_revision = initial.index_revision()
    finally:
        initial.close()
    (repo / "app.py").write_text("def value():\n    return 7\n", encoding="utf-8")

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = [
        context.Process(
            target=_refresh_process,
            args=(str(db), str(repo.resolve()), barrier, queue),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    outcomes = [queue.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert {state for state, _status, _revision in outcomes} <= {
        "current",
        "refreshed",
    }
    assert {status for _state, status, _revision in outcomes} == {None}
    assert {revision for _state, _status, revision in outcomes} == {initial_revision + 1}
    index = ProjectIndex(db)
    try:
        assert index.index_revision() == initial_revision + 1
        indexed_hash = index.conn.execute(
            "SELECT sha256 FROM files WHERE path = 'app.py'"
        ).fetchone()["sha256"]
    finally:
        index.close()
    assert indexed_hash == sha256_text((repo / "app.py").read_text(encoding="utf-8"))


def test_stale_owner_cannot_release_or_renew_new_owners_lease(tmp_path: Path) -> None:
    repo, db = _git_repo(tmp_path)
    coordinator = FreshnessCoordinator(db, lease_seconds=0.1)
    root = str(repo.resolve())

    assert coordinator._acquire_lease(root, "old-owner")
    time.sleep(0.12)
    assert coordinator._acquire_lease(root, "new-owner")
    coordinator._release_lease(root, "old-owner")

    index = ProjectIndex(db)
    try:
        lease = index.conn.execute(
            "SELECT owner FROM refresh_leases WHERE repo_root = ?",
            (root,),
        ).fetchone()
    finally:
        index.close()
    assert lease is not None
    assert lease["owner"] == "new-owner"
    assert not coordinator._renew_lease(root, "old-owner")
    coordinator._release_lease(root, "new-owner")


def test_refresh_that_loses_ownership_cannot_report_current(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, db = _git_repo(tmp_path)
    (repo / "app.py").write_text("def value():\n    return 8\n", encoding="utf-8")

    class LeaseStealingRefresh:
        def __init__(self, db_path: str | Path) -> None:
            self.db_path = db_path

        def refresh(self, **_kwargs):
            index = ProjectIndex(self.db_path)
            try:
                index.conn.execute(
                    """
                    UPDATE refresh_leases
                    SET owner = 'replacement-owner', expires_at = ?
                    """,
                    (time.time() + 5,),
                )
                index.conn.commit()
                revision = index.bump_index_revision()
            finally:
                index.close()
            return SimpleNamespace(
                changed_files=["app.py"],
                deleted_files=[],
                warnings=[],
                revision=revision,
            )

    monkeypatch.setattr(
        freshness_module,
        "RefreshService",
        LeaseStealingRefresh,
    )
    result = FreshnessCoordinator(
        db,
        refresh_timeout=1.0,
        lease_seconds=0.2,
        lease_renew_interval=0.03,
    ).ensure_current(repo)

    assert result.status == "refresh_required"
    assert result.state == "refresh_failed"
    index = ProjectIndex(db)
    try:
        lease = index.conn.execute(
            "SELECT owner FROM refresh_leases WHERE repo_root = ?",
            (str(repo.resolve()),),
        ).fetchone()
    finally:
        index.close()
    assert lease is not None
    assert lease["owner"] == "replacement-owner"


def test_refresh_that_runs_longer_than_the_lease_can_still_succeed_when_ownership_is_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, db = _git_repo(tmp_path)
    (repo / "app.py").write_text("def value():\n    return 11\n", encoding="utf-8")

    class SlowWriteRefresh:
        def __init__(self, db_path: str | Path) -> None:
            self.db_path = db_path

        def refresh(self, **_kwargs):
            index = ProjectIndex(self.db_path)
            try:
                with index.atomic_write():
                    time.sleep(0.25)
                    revision = index.bump_index_revision()
            finally:
                index.close()
            return SimpleNamespace(
                changed_files=["app.py"],
                deleted_files=[],
                warnings=[],
                revision=revision,
            )

    monkeypatch.setattr(freshness_module, "RefreshService", SlowWriteRefresh)
    result = FreshnessCoordinator(
        db,
        refresh_timeout=1.0,
        lease_seconds=0.12,
        lease_renew_interval=0.03,
    ).ensure_current(repo)

    assert result.state == "refreshed"
    assert result.status is None
    assert result.revision == 2


def test_refresh_timeout_returns_requirement_until_background_refresh_finishes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, db = _git_repo(tmp_path)
    (repo / "app.py").write_text("def value():\n    return 10\n", encoding="utf-8")
    started = threading.Event()
    release = threading.Event()

    class BlockingRefresh:
        def __init__(self, db_path: str | Path) -> None:
            self.db_path = db_path

        def refresh(self, **_kwargs):
            started.set()
            assert release.wait(timeout=2)
            index = ProjectIndex(self.db_path)
            try:
                revision = index.bump_index_revision()
            finally:
                index.close()
            return SimpleNamespace(
                changed_files=["app.py"],
                deleted_files=[],
                warnings=[],
                revision=revision,
            )

    monkeypatch.setattr(freshness_module, "RefreshService", BlockingRefresh)
    initial = ProjectIndex(db)
    try:
        initial_revision = initial.index_revision()
    finally:
        initial.close()

    result = FreshnessCoordinator(
        db,
        refresh_timeout=0.05,
        lease_seconds=0.3,
        lease_renew_interval=0.05,
    ).ensure_current(repo)
    assert started.is_set()
    assert result.status == "refresh_required"
    assert result.state == "refreshing"
    assert result.revision == initial_revision

    release.set()
    deadline = time.monotonic() + 2
    revision = initial_revision
    lease = object()
    while time.monotonic() < deadline:
        index = ProjectIndex(db)
        try:
            revision = index.index_revision()
            lease = index.conn.execute("SELECT 1 FROM refresh_leases").fetchone()
        finally:
            index.close()
        if revision == initial_revision + 1 and lease is None:
            break
        time.sleep(0.02)
    assert revision == initial_revision + 1
    assert lease is None


def test_git_freshness_handles_modified_added_renamed_deleted_and_committed_files(
    tmp_path: Path,
) -> None:
    repo, db = _git_repo(tmp_path)
    coordinator = FreshnessCoordinator(db)

    (repo / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    modified = coordinator.ensure_current(repo)
    assert modified.state == "refreshed"

    (repo / "extra.py").write_text("EXTRA = True\n", encoding="utf-8")
    added = coordinator.ensure_current(repo)
    assert added.state == "refreshed"

    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "modify and add")
    committed_without_content_change = coordinator.ensure_current(repo)
    assert committed_without_content_change.state == "current"
    index = ProjectIndex(db)
    try:
        assert index.metadata()["built_commit"] == _git(repo, "rev-parse", "--short=12", "HEAD")
    finally:
        index.close()

    _git(repo, "mv", "app.py", "renamed.py")
    renamed = coordinator.ensure_current(repo)
    assert renamed.state == "refreshed"
    index = ProjectIndex(db)
    try:
        paths = {row["path"] for row in index.conn.execute("SELECT path FROM files")}
    finally:
        index.close()
    assert "app.py" not in paths
    assert "renamed.py" in paths

    (repo / "extra.py").unlink()
    deleted = coordinator.ensure_current(repo)
    assert deleted.state == "refreshed"
    index = ProjectIndex(db)
    try:
        paths = {row["path"] for row in index.conn.execute("SELECT path FROM files")}
    finally:
        index.close()
    assert "extra.py" not in paths


def test_git_freshness_queries_only_changed_and_untracked_paths(tmp_path: Path) -> None:
    repo, db = _git_repo(tmp_path)
    (repo / "app.py").write_text("def value():\n    return 99\n", encoding="utf-8")
    statements: list[str] = []
    index = ProjectIndex(db)
    try:
        index.conn.execute("UPDATE files SET size = ?", (17 * 1024 * 1024,))
        index.conn.commit()
        index.conn.set_trace_callback(statements.append)
        changed = _detect_changed_paths(index, repo, index.metadata())
    finally:
        index.close()

    assert changed == [repo / "app.py"]
    file_queries = [
        statement for statement in statements if "SELECT path, sha256 FROM files" in statement
    ]
    assert file_queries
    assert all(" WHERE path IN " in statement for statement in file_queries)


def test_git_freshness_uses_one_status_command_when_head_is_current(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, db = _git_repo(tmp_path)
    index = ProjectIndex(db)
    try:
        index.conn.execute("UPDATE files SET size = ?", (17 * 1024 * 1024,))
        index.conn.commit()
        metadata = index.metadata()
    finally:
        index.close()

    calls: list[list[str]] = []
    real_run_git_bytes = freshness_module._run_git_bytes

    def recording_run_git_bytes(repo_path: Path, args: list[str]) -> bytes | None:
        calls.append(args)
        return real_run_git_bytes(repo_path, args)

    def fail_text_git(*args, **kwargs):
        raise AssertionError("readable Git metadata should avoid text Git subprocesses")

    monkeypatch.setattr(freshness_module, "_run_git_bytes", recording_run_git_bytes)
    monkeypatch.setattr(freshness_module, "_run_git", fail_text_git)

    index = ProjectIndex(db)
    try:
        changed = _detect_changed_paths(index, repo, metadata)
    finally:
        index.close()

    assert changed == []
    assert calls == [
        [
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--no-renames",
        ]
    ]


def test_git_freshness_empty_status_skips_ignore_and_hash_queries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, db = _git_repo(tmp_path)
    index = ProjectIndex(db)
    try:
        index.conn.execute("UPDATE files SET size = ?", (17 * 1024 * 1024,))
        index.conn.commit()
        metadata = index.metadata()
    finally:
        index.close()

    monkeypatch.setattr(freshness_module, "_git_changed_paths", lambda *args: [])

    def fail_broad_filtering(*args, **kwargs):
        raise AssertionError("empty Git status should not perform broad filtering")

    monkeypatch.setattr(freshness_module, "load_ignore_filter", fail_broad_filtering)
    monkeypatch.setattr(freshness_module, "_stored_file_hashes", fail_broad_filtering)

    index = ProjectIndex(db)
    try:
        changed = _detect_changed_paths(index, repo, metadata)
    finally:
        index.close()

    assert changed == []


def test_tiny_git_freshness_skips_git_change_detection_when_current(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, db = _git_repo(tmp_path)

    def fail_git_change_detection(*args, **kwargs):
        raise AssertionError("tiny current repos should not use Git change detection")

    monkeypatch.setattr(
        freshness_module,
        "_git_changed_paths",
        fail_git_change_detection,
    )
    monkeypatch.setattr(
        freshness_module,
        "_run_git",
        fail_git_change_detection,
    )

    result = FreshnessCoordinator(db).ensure_current(repo)

    assert result.state == "current"


def test_tiny_git_freshness_hashes_same_size_preserved_mtime_edit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, db = _git_repo(tmp_path)
    source = repo / "app.py"
    index = ProjectIndex(db)
    try:
        row = index.conn.execute("SELECT mtime, sha256 FROM files WHERE path = 'app.py'").fetchone()
        stored_mtime = float(row["mtime"])
        original_hash = str(row["sha256"])
    finally:
        index.close()
    replacement = "def value():\n    return 2\n"
    assert len(replacement) == len(source.read_text(encoding="utf-8"))
    source.write_text(replacement, encoding="utf-8")
    os.utime(source, (stored_mtime, stored_mtime))

    def fail_git_change_detection(*args, **kwargs):
        raise AssertionError("tiny same-size edits should be detected without Git")

    monkeypatch.setattr(
        freshness_module,
        "_git_changed_paths",
        fail_git_change_detection,
    )

    result = FreshnessCoordinator(db).ensure_current(repo)

    assert result.state == "refreshed"
    index = ProjectIndex(db)
    try:
        updated_hash = index.conn.execute(
            "SELECT sha256 FROM files WHERE path = 'app.py'"
        ).fetchone()["sha256"]
    finally:
        index.close()
    assert updated_hash != original_hash
    assert updated_hash == sha256_text(source.read_text(encoding="utf-8"))


def test_tiny_git_freshness_treats_size_mismatch_as_changed_without_hashing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, db = _git_repo(tmp_path)
    source = repo / "app.py"
    source.write_text("def value():\n    return 100\n", encoding="utf-8")

    def fail_git_change_detection(*args, **kwargs):
        raise AssertionError("tiny size changes should be detected without Git")

    def fail_freshness_hash(*args, **kwargs):
        raise AssertionError("size mismatch proves content changed before hashing")

    monkeypatch.setattr(freshness_module, "_git_changed_paths", fail_git_change_detection)
    monkeypatch.setattr(freshness_module, "sha256_text", fail_freshness_hash)

    result = FreshnessCoordinator(db).ensure_current(repo)

    assert result.state == "refreshed"


def test_tiny_git_freshness_uses_byte_budget_not_symbol_count(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, db = _git_repo(tmp_path)
    index = ProjectIndex(db)
    try:
        index.conn.execute("UPDATE metadata SET value = '1000000' WHERE key = 'symbol_count'")
        index.conn.commit()
    finally:
        index.close()

    def fail_git_change_detection(*args, **kwargs):
        raise AssertionError("symbol count should not gate tiny freshness")

    monkeypatch.setattr(freshness_module, "_git_changed_paths", fail_git_change_detection)

    result = FreshnessCoordinator(db).ensure_current(repo)

    assert result.state == "current"


def test_tiny_git_freshness_falls_back_when_indexed_bytes_exceed_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, db = _git_repo(tmp_path)
    index = ProjectIndex(db)
    try:
        index.conn.execute("UPDATE files SET size = ?", (17 * 1024 * 1024,))
        index.conn.commit()
    finally:
        index.close()

    def fail_tiny_detector(*args, **kwargs):
        raise AssertionError("large indexed bytes should not use the tiny detector")

    monkeypatch.setattr(freshness_module, "_tiny_filesystem_changed_paths", fail_tiny_detector)
    monkeypatch.setattr(freshness_module, "_git_changed_paths", lambda *args, **kwargs: [])

    index = ProjectIndex(db)
    try:
        changed = _detect_changed_paths(index, repo, index.metadata())
    finally:
        index.close()

    assert changed == []


def test_tiny_git_freshness_detects_untracked_source_without_git_change_detection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, db = _git_repo(tmp_path)
    (repo / "extra.py").write_text("EXTRA = True\n", encoding="utf-8")

    def fail_git_change_detection(*args, **kwargs):
        raise AssertionError("tiny repos should find source additions without Git change detection")

    monkeypatch.setattr(
        freshness_module,
        "_git_changed_paths",
        fail_git_change_detection,
    )

    result = FreshnessCoordinator(db).ensure_current(repo)

    assert result.state == "refreshed"
    index = ProjectIndex(db)
    try:
        paths = {row["path"] for row in index.conn.execute("SELECT path FROM files")}
    finally:
        index.close()
    assert "extra.py" in paths


def test_tiny_git_freshness_ignores_gitignored_untracked_source(
    tmp_path: Path,
) -> None:
    repo, db = _git_repo(tmp_path)
    (repo / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (repo / "ignored.py").write_text("IGNORED = True\n", encoding="utf-8")

    result = FreshnessCoordinator(db).ensure_current(repo)

    assert result.state == "current"
    index = ProjectIndex(db)
    try:
        paths = {row["path"] for row in index.conn.execute("SELECT path FROM files")}
    finally:
        index.close()
    assert "ignored.py" not in paths


def test_tiny_git_freshness_checkpoints_committed_current_content_without_git_subprocess(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, db = _git_repo(tmp_path)
    coordinator = FreshnessCoordinator(db)

    (repo / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    refreshed = coordinator.ensure_current(repo)
    assert refreshed.state == "refreshed"
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "content already indexed")
    expected_commit = _git(repo, "rev-parse", "--short=12", "HEAD")

    def fail_git_subprocess(*args, **kwargs):
        raise AssertionError("tiny current repos should checkpoint without subprocess Git")

    monkeypatch.setattr(freshness_module, "_git_changed_paths", fail_git_subprocess)
    monkeypatch.setattr(freshness_module, "_run_git", fail_git_subprocess)
    monkeypatch.setattr(
        "csegraph._core.index.repository.git_head_state",
        fail_git_subprocess,
    )

    current = coordinator.ensure_current(repo)

    assert current.state == "current"
    index = ProjectIndex(db)
    try:
        metadata = index.metadata()
    finally:
        index.close()
    assert metadata["built_commit"] == expected_commit


def test_git_branch_switch_refreshes_back_to_the_checked_out_content(tmp_path: Path) -> None:
    repo, db = _git_repo(tmp_path)
    coordinator = FreshnessCoordinator(db)
    initial_branch = _git(repo, "branch", "--show-current")

    _git(repo, "checkout", "-qb", "alternate")
    (repo / "app.py").write_text("def value():\n    return 99\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "alternate value")
    alternate = coordinator.ensure_current(repo)
    assert alternate.state == "refreshed"

    _git(repo, "checkout", "-q", initial_branch)
    switched_back = coordinator.ensure_current(repo)
    assert switched_back.state == "refreshed"
    index = ProjectIndex(db)
    try:
        source_hash = index.conn.execute(
            "SELECT sha256 FROM files WHERE path = 'app.py'"
        ).fetchone()["sha256"]
        metadata = index.metadata()
    finally:
        index.close()
    assert source_hash == sha256_text((repo / "app.py").read_text(encoding="utf-8"))
    assert metadata["built_branch"] == initial_branch
    assert metadata["built_commit"] == _git(repo, "rev-parse", "--short=12", "HEAD")


def test_failed_refresh_does_not_checkpoint_unindexed_commit(tmp_path: Path) -> None:
    repo, db = _git_repo(tmp_path)
    index = ProjectIndex(db)
    try:
        indexed_commit = index.metadata()["built_commit"]
    finally:
        index.close()

    (repo / "app.py").write_text("def value():\n    return 5\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "unindexed commit")

    with patch(
        "csegraph._core.index.services._write_parsed_files",
        side_effect=RuntimeError("simulated writer failure"),
    ):
        result = FreshnessCoordinator(db).ensure_current(repo)

    assert result.status == "refresh_required"
    index = ProjectIndex(db)
    try:
        metadata = index.metadata()
    finally:
        index.close()
    assert metadata["built_commit"] == indexed_commit
    assert metadata["built_commit"] != _git(repo, "rev-parse", "HEAD")


def test_non_git_freshness_hashes_only_suspected_metadata_changes(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "plain"
    repo.mkdir()
    source = repo / "plain.py"
    source.write_text("VALUE = 'aa'\n", encoding="utf-8")
    db = tmp_path / "plain.db"
    IndexService(db).index(repo)

    index = ProjectIndex(db)
    try:
        initial_revision = index.index_revision()
        stat = source.stat()
        os.utime(source, (stat.st_atime + 2, stat.st_mtime + 2))
        assert _filesystem_changed_paths(index, repo) == []

        source.write_text("VALUE = 'bb'\n", encoding="utf-8")
        changed_stat = source.stat()
        os.utime(source, (changed_stat.st_atime + 2, changed_stat.st_mtime + 2))
        assert _filesystem_changed_paths(index, repo) == [source]
    finally:
        index.close()

    result = FreshnessCoordinator(db).ensure_current(repo)
    assert result.state == "refreshed"
    assert result.revision == initial_revision + 1
