from __future__ import annotations

import json
import multiprocessing
import time
from pathlib import Path

from csegraph import ContextRequest, ContextService, ContextStatus
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.index.services import IndexService
from csegraph._core.retrieval.adaptive import (
    PLAN_CACHE_LIMIT,
    PLAN_CACHE_TTL_SECONDS,
    _plan_cache_key,
    _store_cached_plan,
)


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def greet(name: str) -> str:\n"
        "    return f'hello {name}'\n",
        encoding="utf-8",
    )
    db = tmp_path / "index.db"
    IndexService(db).index(repo, profile="small")
    return repo, db


def _retrieve_in_process(db: str, repo: str, queue: multiprocessing.queues.Queue) -> None:
    result = ContextService(db).retrieve(
        ContextRequest(repo=repo, task="Explain greet", target="greet")
    )
    queue.put((result.status.value, result.usage["cache"], result.freshness["revision"]))


def test_retrieval_plan_cache_hits_across_processes(tmp_path: Path) -> None:
    repo, db = _repo(tmp_path)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()

    first = context.Process(
        target=_retrieve_in_process,
        args=(str(db), str(repo), queue),
    )
    first.start()
    first_result = queue.get(timeout=10)
    first.join(timeout=10)
    assert first.exitcode == 0

    second = context.Process(
        target=_retrieve_in_process,
        args=(str(db), str(repo), queue),
    )
    second.start()
    second_result = queue.get(timeout=10)
    second.join(timeout=10)
    assert second.exitcode == 0

    assert first_result[0:2] == ("ready", "miss")
    assert second_result[0:2] == ("ready", "hit")
    assert first_result[2] == second_result[2]

    index = ProjectIndex(db)
    try:
        hit_count = index.conn.execute(
            "SELECT hit_count FROM retrieval_plan_cache"
        ).fetchone()["hit_count"]
    finally:
        index.close()
    assert hit_count == 1


def test_revision_change_invalidates_cached_plan_without_deleting_history(
    tmp_path: Path,
) -> None:
    repo, db = _repo(tmp_path)
    service = ContextService(db)
    first = service.retrieve(
        ContextRequest(repo=str(repo), task="Explain greet", target="greet")
    )

    (repo / "app.py").write_text(
        "def greet(name: str) -> str:\n"
        "    return f'HELLO {name}'\n",
        encoding="utf-8",
    )
    second = service.retrieve(
        ContextRequest(repo=str(repo), task="Explain greet", target="greet")
    )

    assert first.status == second.status == ContextStatus.READY
    assert first.usage["cache"] == "miss"
    assert second.usage["cache"] == "miss"
    assert second.freshness["revision"] == first.freshness["revision"] + 1

    index = ProjectIndex(db)
    try:
        revisions = {
            row["index_revision"]
            for row in index.conn.execute(
                "SELECT index_revision FROM retrieval_plan_cache"
            )
        }
    finally:
        index.close()
    assert revisions == {
        first.freshness["revision"],
        second.freshness["revision"],
    }


def test_plan_cache_expires_stale_entries_and_evicts_oldest_used(tmp_path: Path) -> None:
    _repo_path, db = _repo(tmp_path)
    index = ProjectIndex(db)
    try:
        revision = index.index_revision()
        stale_time = time.time() - PLAN_CACHE_TTL_SECONDS - 1
        index.conn.execute(
            """
            INSERT INTO retrieval_plan_cache(
                cache_key, index_revision, plan_json,
                created_at, last_used_at, hit_count
            )
            VALUES('stale', ?, '{}', ?, ?, 0)
            """,
            (revision, stale_time, stale_time),
        )
        now = time.time()
        index.conn.executemany(
            """
            INSERT INTO retrieval_plan_cache(
                cache_key, index_revision, plan_json,
                created_at, last_used_at, hit_count
            )
            VALUES(?, ?, '{}', ?, ?, 0)
            """,
            (
                (f"seed-{position}", revision, now, now + position)
                for position in range(PLAN_CACHE_LIMIT)
            ),
        )
        index.conn.commit()

        _store_cached_plan(index, "newest", revision, {"ranked_ids": []})

        count = index.conn.execute(
            "SELECT COUNT(*) AS count FROM retrieval_plan_cache"
        ).fetchone()["count"]
        stale = index.conn.execute(
            "SELECT 1 FROM retrieval_plan_cache WHERE cache_key = 'stale'"
        ).fetchone()
        oldest = index.conn.execute(
            "SELECT 1 FROM retrieval_plan_cache WHERE cache_key = 'seed-0'"
        ).fetchone()
        newest = index.conn.execute(
            "SELECT plan_json FROM retrieval_plan_cache WHERE cache_key = 'newest'"
        ).fetchone()
    finally:
        index.close()

    assert count == PLAN_CACHE_LIMIT
    assert stale is None
    assert oldest is None
    assert json.loads(newest["plan_json"]) == {"ranked_ids": []}


def test_embedding_availability_is_part_of_plan_cache_identity() -> None:
    without_embeddings = _plan_cache_key(
        7,
        "Explain greet",
        "greet",
        "understand",
        False,
    )
    with_embeddings = _plan_cache_key(
        7,
        "Explain greet",
        "greet",
        "understand",
        True,
    )
    assert without_embeddings != with_embeddings


def test_independent_requests_are_self_contained_but_cursor_deduplicates(
    tmp_path: Path,
) -> None:
    repo, db = _repo(tmp_path)
    service = ContextService(db)
    request = ContextRequest(
        repo=str(repo),
        task="Explain greet",
        target="greet",
    )
    first = service.retrieve(request)
    independent = service.retrieve(request)
    continuation = service.retrieve(
        ContextRequest(
            repo=str(repo),
            task=request.task,
            target=request.target,
            cursor=first.cursor,
        )
    )

    assert first.slices
    assert independent.slices
    assert {
        (item.path, tuple(item.lines or []))
        for item in first.slices
    } == {
        (item.path, tuple(item.lines or []))
        for item in independent.slices
    }
    assert {
        (item.path, tuple(item.lines or []))
        for item in first.slices
    }.isdisjoint(
        {
            (item.path, tuple(item.lines or []))
            for item in continuation.slices
        }
    )


def test_retrieval_history_writes_do_not_advance_revision_or_invalidate_plan(
    tmp_path: Path,
) -> None:
    repo, db = _repo(tmp_path)
    service = ContextService(db)
    first = service.retrieve(
        ContextRequest(repo=str(repo), task="Explain greet", target="greet")
    )
    second = service.retrieve(
        ContextRequest(repo=str(repo), task="Explain greet", target="greet")
    )

    index = ProjectIndex(db)
    try:
        revision = index.index_revision()
        run_count = index.conn.execute(
            "SELECT COUNT(*) AS count FROM retrieval_runs"
        ).fetchone()["count"]
        plan_count = index.conn.execute(
            "SELECT COUNT(*) AS count FROM retrieval_plan_cache"
        ).fetchone()["count"]
    finally:
        index.close()

    assert first.freshness["revision"] == second.freshness["revision"] == revision
    assert first.usage["cache"] == "miss"
    assert second.usage["cache"] == "hit"
    assert run_count == 2
    assert plan_count == 1
