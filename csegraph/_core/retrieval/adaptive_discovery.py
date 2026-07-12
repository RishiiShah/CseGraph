from __future__ import annotations

import re
from typing import Any, Iterable

from csegraph._core.index.repository import ProjectIndex
from csegraph._core.text.query_tokenizer import query_tokenizer

from .adaptive_caps import HARD_MAX_CANDIDATES
from .adaptive_constants import (
    _GENERATED_PATH_PARTS,
    _TEST_WORDS,
)
from .adaptive_ranking import _candidate_sort_key, _deduplicate_ranked_rows


def _discover_candidates(
    index: ProjectIndex,
    task: str,
    target: str | None,
    *,
    candidate_limit: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    candidate_limit = (
        HARD_MAX_CANDIDATES if candidate_limit is None else max(1, int(candidate_limit))
    )
    candidate_scores: dict[str, float] = {}
    candidate_precedence: dict[str, int] = {}
    candidate_fts_rank: dict[str, int] = {}
    candidate_reasons: dict[str, list[str]] = {}
    exact_ids: set[str] = set()
    target_exact = False
    target_rows: list[dict[str, Any]] = []
    if target:
        target_rows, exact = _target_candidate_rows(index, target)
        target_exact = exact
        for position, row in enumerate(target_rows):
            node_id = str(row["id"])
            candidate_scores[node_id] = 100.0 - position
            candidate_precedence[node_id] = 0 if exact else 1
            candidate_reasons[node_id] = [
                "explicit exact target" if exact else "explicit target candidate"
            ]
            if exact:
                exact_ids.add(node_id)

    fts_ids = _fts_candidate_ids(index, task, candidate_limit)
    for position, node_id in enumerate(fts_ids):
        candidate_scores[node_id] = max(
            candidate_scores.get(node_id, 0.0),
            30.0 - (position / max(1, len(fts_ids))) * 10.0,
        )
        candidate_precedence[node_id] = min(candidate_precedence.get(node_id, 9), 3)
        candidate_fts_rank[node_id] = min(
            candidate_fts_rank.get(node_id, candidate_limit),
            position,
        )
        candidate_reasons.setdefault(node_id, []).append("full-text match")

    task_tokens = sorted(
        {token.lower() for token in query_tokenizer.tokenize(task) if len(token) > 2}
    )
    exact_task_names = sorted(
        {
            identifier.casefold()
            for identifier in re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", task)
            if len(identifier) > 2
        }
    )
    if exact_task_names:
        placeholders = ",".join("?" for _ in exact_task_names)
        rows = index.conn.execute(
            f"""
            SELECT id
            FROM symbols
            WHERE LOWER(name) IN ({placeholders})
            LIMIT ?
            """,
            (*exact_task_names, candidate_limit),
        ).fetchall()
        for row in rows:
            node_id = str(row["id"])
            candidate_scores[node_id] = max(candidate_scores.get(node_id, 0.0), 60.0)
            candidate_precedence[node_id] = min(candidate_precedence.get(node_id, 9), 2)
            candidate_reasons.setdefault(node_id, []).append("exact task symbol")
            exact_ids.add(node_id)

    rows_by_id = _load_candidate_rows(index, candidate_scores.keys())
    task_lower = task.lower()
    explicit_target = (target or "").lower()
    task_token_set = set(task_tokens)
    scope_file_ids, imported_file_ids = _task_scope_file_ids(index, task)
    anchor_ids = [str(row["id"]) for row in target_rows] if target_rows else sorted(exact_ids)
    if not anchor_ids and fts_ids:
        anchor_ids = fts_ids[:1]
    graph_near_ids = _directly_connected_candidate_ids(
        index,
        anchor_ids,
        rows_by_id.keys(),
    )
    for node_id, row in rows_by_id.items():
        score = candidate_scores.get(node_id, 0.0)
        name = str(row["name"]).lower()
        path = str(row["path"]).lower()
        if name and name in task_lower:
            score += 20.0
            candidate_reasons.setdefault(node_id, []).append("name mentioned in task")
        if path and path in task_lower:
            score += 15.0
            candidate_reasons.setdefault(node_id, []).append("path mentioned in task")
        if explicit_target and (
            name == explicit_target or path == explicit_target or node_id.lower() == explicit_target
        ):
            score += 50.0
            candidate_precedence[node_id] = 0
            candidate_reasons.setdefault(node_id, []).append("explicit target match")
            exact_ids.add(node_id)
        overlap = set(query_tokenizer.tokenize(f"{name} {path}")) & task_token_set
        score += float(len(overlap)) * 2.0
        file_id = str(row.get("file_id") or "")
        if file_id in scope_file_ids:
            row["_scope_rank"] = 0
            score += 18.0
            candidate_reasons.setdefault(node_id, []).append("same-file task scope")
        elif file_id in imported_file_ids:
            row["_scope_rank"] = 1
            score += 12.0
            candidate_reasons.setdefault(node_id, []).append("imported task scope")
        else:
            row["_scope_rank"] = 2
        if node_id in graph_near_ids:
            row["_graph_rank"] = 0
            score += 6.0
            candidate_reasons.setdefault(node_id, []).append("direct candidate relationship")
        else:
            row["_graph_rank"] = 1
        explicitly_requested = candidate_precedence.get(node_id, 9) == 0
        if (
            bool(row.get("is_test"))
            and not (task_token_set & _TEST_WORDS)
            and not explicitly_requested
        ):
            score -= 24.0
            row["_penalty_rank"] = 1
            candidate_reasons.setdefault(node_id, []).append("test-symbol penalty")
        else:
            row["_penalty_rank"] = 0
        if _is_generated_or_vendor_path(path) and not explicitly_requested:
            score -= 20.0
            row["_penalty_rank"] = max(int(row["_penalty_rank"]), 1)
            candidate_reasons.setdefault(node_id, []).append("generated/vendor penalty")
        row["_score"] = score
        row["_precedence"] = candidate_precedence.get(node_id, 9)
        row["_fts_rank"] = candidate_fts_rank.get(node_id, candidate_limit)
        row["_rank_reasons"] = list(dict.fromkeys(candidate_reasons.get(node_id, [])))

    ranked = sorted(rows_by_id.values(), key=_candidate_sort_key)
    exact = bool(
        ranked
        and (
            (target_exact and str(ranked[0]["id"]) in {str(row["id"]) for row in target_rows})
            or (
                not target
                and str(ranked[0]["id"]) in exact_ids
                and sum(1 for row in ranked if str(row["id"]) in exact_ids) == 1
            )
        )
    )
    return _deduplicate_ranked_rows(ranked)[:candidate_limit], exact


def _target_candidate_rows(
    index: ProjectIndex,
    target: str,
) -> tuple[list[dict[str, Any]], bool]:
    requested = target.strip()
    row = index.conn.execute(
        "SELECT id FROM symbols WHERE id = ? LIMIT 1",
        (requested,),
    ).fetchone()
    if row is not None:
        return [{"id": row["id"]}], True

    exact_name = index.conn.execute(
        """
        SELECT s.id
        FROM symbols AS s
        JOIN files AS f ON f.id = s.file_id
        WHERE LOWER(s.name) = LOWER(?)
        ORDER BY f.path, s.start_line
        LIMIT 9
        """,
        (requested,),
    ).fetchall()
    if exact_name:
        return [dict(item) for item in exact_name], len(exact_name) == 1

    reexported = _reexport_target_candidates(index, requested)
    if reexported:
        return [{"id": node_id} for node_id in reexported], len(reexported) == 1

    qualified = index.conn.execute(
        """
        SELECT s.id
        FROM symbols AS s
        JOIN files AS f ON f.id = s.file_id
        WHERE LOWER(s.id) LIKE '%::' || LOWER(?)
           OR LOWER(s.id) LIKE '%.' || LOWER(?)
        ORDER BY f.path, s.start_line
        LIMIT 9
        """,
        (requested, requested),
    ).fetchall()
    if qualified:
        return [dict(item) for item in qualified], len(qualified) == 1

    normalized_path = requested.replace("\\", "/").lstrip("./")
    exact_path = index.conn.execute(
        """
        SELECT s.id
        FROM symbols AS s
        JOIN files AS f ON f.id = s.file_id
        WHERE LOWER(f.path) = LOWER(?)
        ORDER BY s.start_line
        LIMIT 9
        """,
        (normalized_path,),
    ).fetchall()
    if exact_path:
        return [dict(item) for item in exact_path], len(exact_path) == 1

    fuzzy = index.conn.execute(
        """
        SELECT s.id
        FROM symbols AS s
        JOIN files AS f ON f.id = s.file_id
        WHERE LOWER(s.name) LIKE LOWER(?) OR LOWER(f.path) LIKE LOWER(?)
        ORDER BY LENGTH(s.name), f.path, s.start_line
        LIMIT 9
        """,
        (f"%{requested}%", f"%{normalized_path}%"),
    ).fetchall()
    return [dict(item) for item in fuzzy], False


def _reexport_target_candidates(index: ProjectIndex, requested: str) -> list[str]:
    """Resolve an import alias to its canonical indexed symbol.

    Re-export chains are followed through resolved files, with a small depth cap
    and cycle guard. Multiple canonical destinations stay ambiguous.
    """

    terminal = requested.rsplit(".", 1)[-1]
    rows = index.conn.execute(
        """
        SELECT file_id, local_name, imported_name, qualified_name,
               resolved_file_id, resolved_symbol_id
        FROM import_bindings
        WHERE LOWER(local_name) = LOWER(?)
           OR LOWER(qualified_name) = LOWER(?)
        ORDER BY file_id, start_line, local_name
        LIMIT 16
        """,
        (terminal, requested),
    ).fetchall()
    candidates: set[str] = set()
    for row in rows:
        candidates.update(_binding_target_ids(index, dict(row), visited=set(), depth=0))
    return sorted(candidates)


def _binding_target_ids(
    index: ProjectIndex,
    binding: dict[str, Any],
    *,
    visited: set[tuple[str, str]],
    depth: int,
) -> set[str]:
    resolved_symbol = str(binding.get("resolved_symbol_id") or "")
    if resolved_symbol:
        return {resolved_symbol}
    resolved_file = str(binding.get("resolved_file_id") or "")
    imported_name = str(binding.get("imported_name") or "")
    if not resolved_file or not imported_name or depth >= 4:
        return set()
    visit_key = (resolved_file, imported_name.casefold())
    if visit_key in visited:
        return set()
    next_visited = {*visited, visit_key}

    direct = set(_symbol_ids_in_file(index, resolved_file, imported_name))
    if direct:
        return direct

    rows = index.conn.execute(
        """
        SELECT file_id, local_name, imported_name, qualified_name,
               resolved_file_id, resolved_symbol_id
        FROM import_bindings
        WHERE file_id = ? AND LOWER(local_name) = LOWER(?)
        ORDER BY start_line, local_name
        LIMIT 8
        """,
        (resolved_file, imported_name),
    ).fetchall()
    candidates: set[str] = set()
    for row in rows:
        candidates.update(
            _binding_target_ids(
                index,
                dict(row),
                visited=next_visited,
                depth=depth + 1,
            )
        )
    return candidates


def _symbol_ids_in_file(index: ProjectIndex, file_id: str, name: str) -> list[str]:
    rows = index.conn.execute(
        """
        SELECT s.id
        FROM symbols AS s
        JOIN files AS f ON f.id = s.file_id
        WHERE s.file_id = ?
          AND (
              LOWER(s.name) = LOWER(?)
              OR LOWER(s.name) LIKE '%.' || LOWER(?)
          )
        ORDER BY f.path, s.start_line, s.id
        LIMIT 3
        """,
        (file_id, name, name),
    ).fetchall()
    return [str(row["id"]) for row in rows]


def _fts_candidate_ids(index: ProjectIndex, task: str, limit: int) -> list[str]:
    tokens = [token for token in query_tokenizer.tokenize(task) if token and len(token) > 1]
    if not tokens:
        return []
    expression = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)
    try:
        rows = index.conn.execute(
            """
            SELECT node_id
            FROM lexical_index
            WHERE lexical_index MATCH ?
            ORDER BY bm25(lexical_index, 8.0, 4.0, 2.0, 1.0, 2.0, 1.0)
            LIMIT ?
            """,
            (expression, limit),
        ).fetchall()
    except Exception:
        return []
    return [str(row["node_id"]) for row in rows]


def _load_candidate_rows(
    index: ProjectIndex,
    node_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    unique = list(dict.fromkeys(str(node_id) for node_id in node_ids if node_id))
    if not unique:
        return {}
    placeholders = ",".join("?" for _ in unique)
    rows = index.conn.execute(
        f"""
        SELECT
            s.id, s.file_id, s.parent_id, s.kind, s.name, f.path, f.language, s.signature,
            s.docstring, s.start_line, s.end_line, s.source_hash, s.is_test,
            COALESCE(sm.summary, '') AS summary
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        LEFT JOIN summaries sm ON sm.node_id = s.id
        WHERE s.id IN ({placeholders})
        """,
        tuple(unique),
    ).fetchall()
    return {str(row["id"]): dict(row) for row in rows}


def _task_scope_file_ids(
    index: ProjectIndex,
    task: str,
) -> tuple[set[str], set[str]]:
    mentions = list(
        dict.fromkeys(
            match.replace("\\", "/").strip("./")
            for match in re.findall(
                r"(?:[A-Za-z0-9_.-]+[/\\])*[A-Za-z0-9_.-]+\.[A-Za-z0-9_+-]+",
                task,
            )
            if match
        )
    )[:16]
    if not mentions:
        return set(), set()

    path_mentions = [mention for mention in mentions if "/" in mention]
    name_mentions = [mention.rsplit("/", 1)[-1] for mention in mentions]
    clauses: list[str] = []
    parameters: list[str | int] = []
    if path_mentions:
        placeholders = ",".join("?" for _ in path_mentions)
        clauses.append(f"path IN ({placeholders})")
        parameters.extend(path_mentions)
    if name_mentions:
        placeholders = ",".join("?" for _ in name_mentions)
        clauses.append(f"name IN ({placeholders})")
        parameters.extend(name_mentions)
    parameters.append(32)
    scoped = {
        str(row["id"])
        for row in index.conn.execute(
            f"""
            SELECT id
            FROM files
            WHERE {" OR ".join(clauses)}
            ORDER BY path
            LIMIT ?
            """,
            tuple(parameters),
        )
    }
    if not scoped:
        return set(), set()
    placeholders = ",".join("?" for _ in scoped)
    imported = {
        str(row["resolved_file_id"])
        for row in index.conn.execute(
            f"""
            SELECT DISTINCT resolved_file_id
            FROM imports
            WHERE file_id IN ({placeholders}) AND resolved_file_id IS NOT NULL
            """,
            tuple(sorted(scoped)),
        )
    }
    return scoped, imported


def _directly_connected_candidate_ids(
    index: ProjectIndex,
    anchor_ids: Iterable[str],
    node_ids: Iterable[str],
) -> set[str]:
    anchors = list(dict.fromkeys(str(node_id) for node_id in anchor_ids if node_id))
    candidates = {str(node_id) for node_id in node_ids if node_id}
    if not anchors or len(candidates) < 2:
        return set()
    placeholders = ",".join("?" for _ in anchors)
    rows = index.conn.execute(
        f"""
        SELECT source, target
        FROM edges
        WHERE (source IN ({placeholders}) OR target IN ({placeholders}))
          AND relation IN ('calls', 'decorates', 'imports', 'inherits', 'tested_by')
        ORDER BY source, target, relation
        LIMIT 128
        """,
        (*anchors, *anchors),
    ).fetchall()
    connected: set[str] = set()
    for row in rows:
        source = str(row["source"])
        target = str(row["target"])
        if source in anchors and target in candidates:
            connected.add(target)
        if target in anchors and source in candidates:
            connected.add(source)
    return connected


def _is_generated_or_vendor_path(path: str) -> bool:
    normalized = path.lower().replace("\\", "/")
    parts = {part for part in normalized.split("/") if part}
    filename = normalized.rsplit("/", 1)[-1]
    return bool(
        parts & _GENERATED_PATH_PARTS
        or ".generated." in filename
        or filename.endswith((".g.py", ".g.ts", ".min.js"))
    )
