from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from csegraph_core.core.models import KeyEntity, MinimalResult, NextToolSuggestion
from csegraph_core.index.repository import ProjectIndex


_KEY_ENTITY_LIMIT = 5

_INTENT_KEYWORDS: List[tuple[str, tuple[str, ...]]] = [
    ("review", ("review", "pr", "merge", "diff", "changes", "changeset")),
    ("debug", ("debug", "bug", "error", "fix", "broken", "failing", "crash", "regression")),
    ("refactor", ("refactor", "rename", "extract", "cleanup", "dead", "unused", "remove")),
    ("explore", ("explore", "understand", "architecture", "overview", "map", "learn", "what is", "how does")),
]


class MinimalService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def first(self, task: Optional[str] = None) -> MinimalResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata.get("root_dir", "")

            totals = _graph_totals(index)
            key_entities = _top_entities(index, _KEY_ENTITY_LIMIT)
            languages = _top_languages(index)

            summary = _format_summary(totals, languages)
            intent = _detect_intent(task)
            suggestions = _suggestions_for_intent(intent, task)

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
            )
            preview.estimated_tokens = _estimate_tokens(preview)
            return preview
        finally:
            index.close()


def _graph_totals(index: ProjectIndex) -> Dict[str, int]:
    rows = index.conn.execute(
        "SELECT type, COUNT(*) as c FROM nodes GROUP BY type"
    ).fetchall()
    counts = {row["type"]: int(row["c"]) for row in rows}
    total_edges = index.conn.execute("SELECT COUNT(*) AS c FROM edges").fetchone()
    return {
        "files": counts.get("file", 0),
        "symbols": sum(counts.get(t, 0) for t in ("class", "function", "method", "test")),
        "nodes": sum(counts.values()),
        "edges": int(total_edges["c"]) if total_edges else 0,
    }


def _top_languages(index: ProjectIndex, limit: int = 3) -> List[str]:
    rows = index.conn.execute(
        """
        SELECT language, COUNT(*) AS c
        FROM nodes
        WHERE language IS NOT NULL AND language != ''
        GROUP BY language
        ORDER BY c DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [row["language"] for row in rows if row["language"]]


def _top_entities(index: ProjectIndex, limit: int) -> List[KeyEntity]:
    rows = index.conn.execute(
        """
        SELECT n.id, n.name, n.type, n.path, COUNT(e.source) AS degree
        FROM nodes n
        LEFT JOIN edges e ON e.source = n.id OR e.target = n.id
        WHERE n.type IN ('class', 'function', 'method', 'test')
        GROUP BY n.id
        ORDER BY degree DESC, n.name ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        KeyEntity(
            id=row["id"],
            name=row["name"] or row["id"],
            kind=row["type"],
            path=row["path"] or "",
            degree=int(row["degree"] or 0),
        )
        for row in rows
    ]


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


def _suggestions_for_intent(intent: str, task: Optional[str]) -> List[NextToolSuggestion]:
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
    from csegraph_core.core.serializer import to_dict

    payload = to_dict(result)
    payload.pop("estimated_tokens", None)
    serialized = json.dumps(payload, separators=(",", ":"))
    return max(1, math.ceil(len(serialized) / 2.7))
