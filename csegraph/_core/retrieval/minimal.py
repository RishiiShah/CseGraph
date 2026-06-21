from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from csegraph._core.core.models import IndexHealth, KeyEntity, MinimalResult, NextToolSuggestion
from csegraph._core.corpus_health import (
    assess_index_health,
    collect_index_metrics,
    index_age_hours,
)
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.repo_state import git_head_state
from csegraph._core.status import _build_warnings

_KEY_ENTITY_LIMIT = 5
_HUB_FLOOR = 50
_HUB_PERCENTILE = 0.99
_HUB_CACHE_MAX = 32
_hub_cache: Dict[Tuple[str, int], Tuple[int, FrozenSet[str]]] = {}

_INTENT_KEYWORDS: List[tuple[str, tuple[str, ...]]] = [
    ("review", ("review", "pr", "merge", "diff", "changes", "changeset")),
    ("debug", ("debug", "bug", "error", "fix", "broken", "failing", "crash", "regression")),
    ("refactor", ("refactor", "rename", "extract", "cleanup", "dead", "unused", "remove")),
    (
        "explore",
        (
            "explore",
            "understand",
            "architecture",
            "overview",
            "map",
            "learn",
            "what is",
            "how does",
        ),
    ),
]


def clear_hub_cache() -> None:
    _hub_cache.clear()


class MinimalService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def first(
        self, task: Optional[str] = None, inferred_intent: Optional[str] = None
    ) -> MinimalResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata.get("root_dir", "")

            from csegraph._core.retrieval.cache import CACHE

            snapshot = CACHE.get_snapshot(index)

            totals = _graph_totals(snapshot)
            _, hubs = _cached_snapshot_hub_info(snapshot)
            intent = inferred_intent if inferred_intent is not None else _detect_intent(task)
            key_entities = _top_entities(
                snapshot,
                _KEY_ENTITY_LIMIT,
                exclude=hubs,
                include_tests=_include_test_entities(intent, task),
            )
            languages = _top_languages(snapshot)

            summary = _format_summary(totals, languages)
            metrics = collect_index_metrics(index.conn)
            meta = metadata
            current_branch, current_commit = (
                git_head_state(repo_root)
                if repo_root and Path(repo_root).exists()
                else (None, None)
            )
            ext_warnings = _build_warnings(meta, repo_root, current_branch, current_commit)
            age_h = index_age_hours(metadata_updated_at=meta.get("updated_at"), conn=index.conn)
            health = assess_index_health(
                metrics,
                index_age_hours=age_h,
                external_warnings=ext_warnings,
            )
            if health.verdict != "ok":
                summary = health.summary + "\n" + summary
            suggested_queries = (
                _explore_suggested_queries(key_entities) if intent == "explore" else []
            )
            suggestions = _suggestions_for_intent(intent, task, health)

            preview = MinimalResult(
                command="minimal",
                db_path=self.db_path,
                repo_root=repo_root,
                summary=summary,
                task=task,
                task_intent=intent,
                key_entities=key_entities,
                next_tool_suggestions=suggestions,
                estimated_tokens=0,
                index_health=health,
                suggested_queries=suggested_queries,
            )
            preview.estimated_tokens = _estimate_tokens(preview)
            return preview
        finally:
            index.close()


def _graph_totals(snapshot: Any) -> Dict[str, int]:
    files = len(snapshot.files)
    symbols = len(snapshot.symbols_light)
    nodes = len(snapshot.node_rows_light)
    edges = sum(len(edges) for edges in snapshot.outgoing.values())
    return {
        "files": files,
        "symbols": symbols,
        "nodes": nodes,
        "edges": edges,
    }


def _top_languages(snapshot: Any, limit: int = 3) -> List[str]:
    counts: Dict[str, int] = {}
    for node in snapshot.node_rows_light.values():
        lang = node.get("language")
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return [lang for lang, _ in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]]


def _cached_snapshot_hub_info(snapshot: Any) -> Tuple[int, set[str]]:
    key = (snapshot.db_path, snapshot.data_version)
    cached = _hub_cache.get(key)
    if cached is not None:
        return cached[0], set(cached[1])
    if len(_hub_cache) >= _HUB_CACHE_MAX:
        _hub_cache.clear()
    threshold, hubs = _snapshot_hub_info(snapshot)
    _hub_cache[key] = (threshold, frozenset(hubs))
    return threshold, hubs


def _snapshot_hub_info(snapshot: Any) -> Tuple[int, set[str]]:
    degree: Dict[str, int] = {}
    for edges in snapshot.outgoing.values():
        for edge in edges:
            source = edge["source"]
            target = edge["target"]
            degree[source] = degree.get(source, 0) + 1
            degree[target] = degree.get(target, 0) + 1
    if not degree:
        return _HUB_FLOOR, set()

    degrees = sorted(degree.values())
    idx = max(0, math.ceil(_HUB_PERCENTILE * len(degrees)) - 1)
    threshold = max(degrees[idx], _HUB_FLOOR)
    hubs = {node_id for node_id, count in degree.items() if count >= threshold}
    return threshold, hubs


def _top_entities(
    snapshot: Any,
    limit: int,
    exclude: Optional[set[str]] = None,
    include_tests: bool = True,
) -> List[KeyEntity]:
    exclude_ids = exclude or set()

    candidates = []
    for nid, node in snapshot.symbols_light.items():
        if nid in exclude_ids:
            continue
        kind = node.get("kind") or node.get("type") or ""
        if not include_tests:
            if kind == "test" or node.get("is_test"):
                continue
            path = node.get("file_path") or ""
            if path.startswith("tests/") or path.startswith("test/"):
                continue

        degree = len(snapshot.outgoing.get(nid, [])) + len(snapshot.incoming.get(nid, []))
        candidates.append(
            {
                "id": nid,
                "name": node.get("name") or nid,
                "kind": kind,
                "path": node.get("file_path") or "",
                "degree": degree,
            }
        )

    candidates.sort(key=lambda x: (-x["degree"], x["name"]))

    return [
        KeyEntity(
            id=c["id"],
            name=c["name"],
            kind=c["kind"],
            path=c["path"],
            degree=c["degree"],
        )
        for c in candidates[:limit]
    ]


def _include_test_entities(intent: str, task: Optional[str]) -> bool:
    if intent in {"debug", "review"}:
        return True
    lowered = (task or "").lower()
    test_words = ("test", "tests", "pytest", "failing", "failure", "coverage")
    return any(re.search(rf"\b{word}\b", lowered) for word in test_words)


def _format_summary(totals: Dict[str, int], languages: List[str]) -> str:
    lang_part = f" languages: {', '.join(languages)}." if languages else ""
    return (
        f"{totals['files']} files, {totals['symbols']} symbols, {totals['edges']} edges."
        + lang_part
    )


def _explore_suggested_queries(entities: List[KeyEntity], limit: int = 2) -> List[str]:
    queries: List[str] = []
    for entity in entities[:limit]:
        name = entity.name or entity.id
        path = entity.path or "this area"
        queries.append(f"How does {name} connect to the rest of the codebase (see {path})?")
    return queries


def _detect_intent(task: Optional[str]) -> str:
    if not task:
        return "general"
    lowered = task.lower()
    for intent, keywords in _INTENT_KEYWORDS:
        for keyword in keywords:
            if re.search(rf"\b{re.escape(keyword)}\b", lowered):
                return intent
    return "general"


def _suggestions_for_intent(
    intent: str,
    task: Optional[str],
    health: Optional[IndexHealth] = None,
) -> List[NextToolSuggestion]:
    task_arg = task or ""
    if health and health.verdict in ("stale", "rebuild") and intent != "explore":
        return [
            NextToolSuggestion(
                tool="csegraph_refresh" if health.verdict == "stale" else "csegraph_index",
                reason=health.hints[0] if health.hints else health.summary,
                args={},
            ),
            NextToolSuggestion(
                tool="csegraph_context",
                reason="After the index is current, fetch task context at detail_level=auto.",
                args={"task": task_arg, "detail_level": "auto"},
            ),
        ]
    if intent == "review":
        return [
            NextToolSuggestion(
                tool="csegraph_refresh",
                reason="Refresh the index before reviewing so changed files are reflected.",
                args={},
            ),
            NextToolSuggestion(
                tool="csegraph_context",
                reason="Retrieve task-specific context with detail_level=auto.",
                args={"task": task_arg, "detail_level": "auto"},
            ),
        ]
    if intent == "debug":
        return [
            NextToolSuggestion(
                tool="csegraph_context",
                reason="Pull task context; pass the failing symbol or file as target if known.",
                args={"task": task_arg, "detail_level": "auto"},
            ),
            NextToolSuggestion(
                tool="csegraph_graph",
                reason="Inspect the failing symbol's neighborhood (detail_level=standard) to map callers.",
                args={"detail_level": "minimal"},
            ),
        ]
    if intent == "refactor":
        return [
            NextToolSuggestion(
                tool="csegraph_graph",
                reason="Map dependencies and callers of the target before renaming or removing.",
                args={"detail_level": "minimal"},
            ),
            NextToolSuggestion(
                tool="csegraph_path",
                reason="Check connectivity between two symbols when extracting or merging.",
                args={"detail_level": "minimal"},
            ),
        ]
    if intent == "explore":
        return [
            NextToolSuggestion(
                tool="csegraph_graph",
                reason="Inspect a high-degree key entity at depth 2 for an architecture sketch.",
                args={"depth": 2, "detail_level": "minimal"},
            ),
            NextToolSuggestion(
                tool="csegraph_context",
                reason="Follow up with task-specific context once a subsystem is chosen.",
                args={"task": task_arg, "detail_level": "auto"},
            ),
        ]
    return [
        NextToolSuggestion(
            tool="csegraph_context",
            reason="Start with task-specific context at detail_level=auto.",
            args={"task": task_arg, "detail_level": "auto"},
        ),
    ]


def _estimate_tokens(result: MinimalResult) -> int:
    from csegraph._core.core.serializer import to_dict

    payload = to_dict(result)
    payload.pop("estimated_tokens", None)
    serialized = json.dumps(payload, separators=(",", ":"))
    return max(1, math.ceil(len(serialized) / 2.7))
