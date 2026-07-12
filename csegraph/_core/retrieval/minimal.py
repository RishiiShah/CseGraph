from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from csegraph._core.core.models import KeyEntity, MinimalResult, NextToolSuggestion
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.retrieval.freshness import FreshnessCoordinator

_KEY_ENTITY_LIMIT = 3
_HUB_FLOOR = 50
_HUB_PERCENTILE = 0.99

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
            "improve",
            "improvement",
            "roadmap",
        ),
    ),
]


class MinimalService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def first(
        self,
        task: Optional[str] = None,
        repo: str | Path | None = None,
    ) -> MinimalResult:
        intent = _detect_intent(task)
        if repo is not None:
            freshness = FreshnessCoordinator(self.db_path).ensure_current(repo)
            if freshness.status is not None:
                suggestions = []
                if freshness.next:
                    suggestions.append(
                        NextToolSuggestion(
                            tool=str(freshness.next.get("tool") or "csegraph_index"),
                            reason=str(
                                freshness.next.get("reason") or "Make the repository index current."
                            ),
                            args=dict(freshness.next.get("arguments") or {}),
                        )
                    )
                return MinimalResult(
                    summary=f"Index state: {freshness.status}.",
                    key_entities=[],
                    next_tool_suggestions=suggestions[:1],
                )
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            totals = _graph_totals(index.conn)
            key_entities = _top_entities(
                index.conn,
                _KEY_ENTITY_LIMIT,
                include_tests=_include_test_entities(intent, task),
            )
            languages = _top_languages(index.conn)

            summary = _format_summary(totals, languages)
            suggestions = _suggestions_for_intent(intent, task)

            return MinimalResult(
                summary=summary,
                key_entities=key_entities,
                next_tool_suggestions=suggestions,
            )
        finally:
            index.close()


def _graph_totals(conn: sqlite3.Connection) -> Dict[str, int]:
    row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM files) AS files,
            (SELECT COUNT(*) FROM symbols) AS symbols,
            (SELECT COUNT(*) FROM entities) AS nodes,
            (SELECT COUNT(*) FROM edges) AS edges
        """
    ).fetchone()
    assert row is not None
    return {
        "files": int(row["files"]),
        "symbols": int(row["symbols"]),
        "nodes": int(row["nodes"]),
        "edges": int(row["edges"]),
    }


def _top_languages(conn: sqlite3.Connection, limit: int = 3) -> List[str]:
    rows = conn.execute(
        """
        SELECT language, COUNT(*) AS entity_count
        FROM entities
        WHERE language <> ''
        GROUP BY language
        ORDER BY entity_count DESC, language ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [str(row["language"]) for row in rows]


def _top_entities(
    conn: sqlite3.Connection,
    limit: int,
    include_tests: bool = True,
) -> List[KeyEntity]:
    bounded_limit = min(max(0, int(limit)), _KEY_ENTITY_LIMIT)
    if bounded_limit == 0:
        return []
    rows = conn.execute(
        """
        WITH degree_events(id) AS (
            SELECT source FROM edges
            UNION ALL
            SELECT target FROM edges
        ),
        degrees AS (
            SELECT id, COUNT(*) AS degree
            FROM degree_events
            GROUP BY id
        ),
        ranked_degrees AS (
            SELECT
                degree,
                ROW_NUMBER() OVER (ORDER BY degree) AS position,
                COUNT(*) OVER () AS total
            FROM degrees
        ),
        percentile AS (
            SELECT COALESCE(
                MAX(
                    CASE
                        WHEN position = CAST((? * total) + 0.999999 AS INTEGER)
                        THEN degree
                    END
                ),
                0
            ) AS degree
            FROM ranked_degrees
        ),
        hub_threshold AS (
            SELECT MAX(degree, ?) AS degree
            FROM percentile
        )
        SELECT
            s.id,
            s.name,
            s.kind,
            f.path,
            COALESCE(d.degree, 0) AS degree
        FROM symbols AS s
        JOIN files AS f ON f.id = s.file_id
        LEFT JOIN degrees AS d ON d.id = s.id
        CROSS JOIN hub_threshold AS h
        WHERE COALESCE(d.degree, 0) < h.degree
          AND (
              ?
              OR (
                  s.kind <> 'test'
                  AND s.is_test = 0
                  AND f.path NOT LIKE 'tests/%'
                  AND f.path NOT LIKE 'test/%'
              )
          )
        ORDER BY degree DESC, s.name ASC, s.id ASC
        LIMIT ?
        """,
        (_HUB_PERCENTILE, _HUB_FLOOR, int(include_tests), bounded_limit),
    ).fetchall()
    return [
        KeyEntity(
            id=str(row["id"]),
            name=str(row["name"]),
            kind=str(row["kind"]),
            path=str(row["path"]),
            degree=int(row["degree"]),
        )
        for row in rows
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
) -> List[NextToolSuggestion]:
    task_arg = task or ""
    if intent == "review":
        return [
            NextToolSuggestion(
                tool="csegraph_refresh",
                reason="Refresh the index before reviewing so changed files are reflected.",
                args={},
            ),
            NextToolSuggestion(
                tool="csegraph_context",
                reason="Retrieve adaptive task-specific context.",
                args={"task": task_arg},
            ),
        ]
    if intent == "debug":
        return [
            NextToolSuggestion(
                tool="csegraph_context",
                reason="Pull task context; pass the failing symbol or file as target if known.",
                args={"task": task_arg},
            ),
            NextToolSuggestion(
                tool="csegraph_graph",
                reason="Inspect the failing symbol's neighborhood to map callers.",
                args={},
            ),
        ]
    if intent == "refactor":
        return [
            NextToolSuggestion(
                tool="csegraph_graph",
                reason="Map dependencies and callers of the target before renaming or removing.",
                args={},
            ),
            NextToolSuggestion(
                tool="csegraph_path",
                reason="Check connectivity between two symbols when extracting or merging.",
                args={},
            ),
        ]
    if intent == "explore":
        if _is_broad_context_task(task):
            return [
                NextToolSuggestion(
                    tool="csegraph_context",
                    reason=(
                        "Retrieve task-specific subsystem context before choosing a narrow symbol."
                    ),
                    args={"task": task_arg},
                ),
                NextToolSuggestion(
                    tool="csegraph_graph",
                    reason=(
                        "Inspect a high-degree key entity at depth 2 for an architecture sketch."
                    ),
                    args={"depth": 2},
                ),
            ]
        return [
            NextToolSuggestion(
                tool="csegraph_graph",
                reason="Inspect a high-degree key entity at depth 2 for an architecture sketch.",
                args={"depth": 2},
            ),
            NextToolSuggestion(
                tool="csegraph_context",
                reason="Follow up with task-specific context once a subsystem is chosen.",
                args={"task": task_arg},
            ),
        ]
    return [
        NextToolSuggestion(
            tool="csegraph_context",
            reason="Start with adaptive task-specific context.",
            args={"task": task_arg},
        ),
    ]


def _is_broad_context_task(task: Optional[str]) -> bool:
    lowered = (task or "").lower()
    return any(
        re.search(rf"\b{re.escape(keyword)}\b", lowered)
        for keyword in (
            "improve",
            "improvement",
            "improvements",
            "roadmap",
            "architecture",
            "context engine",
            "retrieval",
        )
    )
