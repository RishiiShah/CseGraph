from __future__ import annotations

import ast
import json
import textwrap
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from csegraph._core.config.profiles import get_profile, resolve_profile_name
from csegraph._core.core.ids import file_node_id, folder_node_id, repo_node_id
from csegraph._core.core.models import IndexResult, RefreshResult
from csegraph._core.discovery import is_discoverable_rel_path, iter_discoverable_rel_paths
from csegraph._core.ignore import load_ignore_filter
from csegraph._core.index.cache import ExtractionCache
from csegraph._core.index.migrations import migrate_schema
from csegraph._core.index.repository import ProjectIndex, json_dumps
from csegraph._core.index.schema import SCHEMA_VERSION
from csegraph._core.languages.registry import UnsupportedLanguageError, registry
from csegraph._core.languages.treesitter.languages import LANGUAGE_SPECS, is_language_available
from csegraph._core.languages.types import (
    ParsedFile,
    ParsedImport,
    ParsedReference,
    ParsedSymbol,
)

_STRUCTURAL_LANGUAGE = "non_code"
_EXTRACTED = "EXTRACTED"


@dataclass
class _WriteBatch:
    summary_rows: List[tuple] = field(default_factory=list)
    lexical_delete_ids: List[tuple] = field(default_factory=list)
    lexical_rows: List[tuple] = field(default_factory=list)
    symbol_by_name: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))
    node_to_file_node: Dict[str, str] = field(default_factory=dict)
    node_kind_by_id: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _ImportBinding:
    import_name: str
    local_name: str
    imported_name: str
    qualified_name: Optional[str]
    binding_kind: str
    resolved_file_id: Optional[str]
    resolved_symbol_id: Optional[str]
    resolution_status: str
    start_line: int
    end_line: int
    source: str
    metadata: Dict[str, object]


@dataclass(frozen=True)
class _TargetResolution:
    target: Optional[str]
    status: str
    strategy: str
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class _AssertionEvidence:
    kind: str
    expression: str
    start_line: int
    end_line: int
    call_names: tuple[str, ...]


class IndexService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def index(
        self,
        repo: str | Path,
        profile: str = "small",
        *,
        exclude_patterns: Optional[Sequence[str]] = None,
        include_roots: Optional[Sequence[str | Path]] = None,
    ) -> IndexResult:
        timings_ms: Dict[str, float] = {}
        repo_root_path = Path(repo).resolve()
        repo_root = str(repo_root_path)
        include_prefixes = _normalize_include_roots(repo_root_path, include_roots)
        cache_path = str(Path(self.db_path).with_name("parse_cache.db"))
        cache = ExtractionCache(cache_path)
        start = time.perf_counter()
        warnings = _missing_optional_language_warnings(
            repo_root_path,
            exclude_patterns=exclude_patterns,
            include_roots=include_prefixes,
        )
        parsed_files = _parse_with_cache(
            _filter_included_files(
                registry.iter_files(repo_root_path, exclude_patterns=exclude_patterns),
                repo_root_path,
                include_prefixes,
            ),
            repo_root_path,
            cache,
        )
        timings_ms["discover_parse"] = _elapsed_ms(start)
        config = get_profile(
            resolve_profile_name(
                profile,
                repo_root=repo_root_path,
                source_file_count=len(parsed_files),
            )
        )

        index = ProjectIndex(self.db_path)
        try:
            start = time.perf_counter()
            index.initialize_schema(reset_on_unsupported=True)
            index.set_metadata(repo_root, config.name, include_roots=include_prefixes)
            timings_ms["initialize_schema"] = _elapsed_ms(start)

            start = time.perf_counter()
            index.clear_graph()
            _clear_impact_index(index)
            timings_ms["clear_graph"] = _elapsed_ms(start)

            start = time.perf_counter()
            stats = _write_parsed_files(index, repo_root, parsed_files)
            timings_ms["write_graph"] = _elapsed_ms(start)

            start = time.perf_counter()
            parse_errors = {
                parsed.rel_path: parsed.parse_error or ""
                for parsed in parsed_files
                if parsed.parse_status != "ok"
            }
            timings_ms["parse_errors"] = _elapsed_ms(start)
            return IndexResult(
                command="index",
                db_path=self.db_path,
                repo_root=repo_root,
                profile=config.name,
                files_indexed=len(parsed_files),
                symbols_indexed=stats["symbols"],
                edges_indexed=stats["edges"],
                cache_hits=cache.hits,
                cache_misses=cache.misses,
                changed_files=[parsed.rel_path for parsed in parsed_files],
                parse_errors=parse_errors,
                warnings=warnings,
                timings_ms=timings_ms,
            )
        finally:
            index.close()
            cache.close()
            from csegraph._core.retrieval.cache import CACHE

            CACHE.clear(self.db_path)


class RefreshService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def refresh(
        self,
        profile: str = "small",
        changed_paths: Optional[Iterable[str | Path]] = None,
        dependents_limit: int = 50,
        *,
        exclude_patterns: Optional[Sequence[str]] = None,
        include_roots: Optional[Sequence[str | Path]] = None,
    ) -> RefreshResult:
        cache_path = str(Path(self.db_path).with_name("parse_cache.db"))
        cache = ExtractionCache(cache_path)
        index = ProjectIndex(self.db_path)
        timings_ms: Dict[str, float] = {}
        try:
            _migrate_supported_schema(index)
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = Path(metadata["root_dir"]).resolve()
            include_prefixes = (
                _normalize_include_roots(repo_root, include_roots)
                if include_roots is not None
                else _include_roots_from_metadata(metadata)
            )
            config = get_profile(
                resolve_profile_name(
                    profile,
                    repo_root=repo_root,
                    source_file_count=_indexed_file_count(index),
                )
            )
            index.set_metadata(str(repo_root), config.name, include_roots=include_prefixes)

            start = time.perf_counter()
            if changed_paths is not None:
                ignore = load_ignore_filter(repo_root, exclude_patterns=exclude_patterns)
                changed_abs_set = set()
                for p in changed_paths:
                    try:
                        resolved_p = Path(p).resolve()
                        if resolved_p.is_relative_to(repo_root):
                            changed_abs_set.add(resolved_p)
                    except Exception:
                        pass

                stored = {
                    row["path"]: row["sha256"]
                    for row in index.conn.execute(
                        "SELECT path, sha256 FROM nodes WHERE type = 'file'"
                    )
                }

                current_files = {}
                deleted = []
                skipped_optional_language_files: List[str] = []
                for path in changed_abs_set:
                    if path.exists() and path.is_file():
                        rel = path.relative_to(repo_root).as_posix()
                        if not _is_included_rel_path(
                            rel, include_prefixes
                        ) or not is_discoverable_rel_path(rel, ignore):
                            if rel in stored:
                                deleted.append(rel)
                            continue
                        try:
                            parser = registry.for_extension(path.suffix)
                            current_files[rel] = (parser, path)
                        except UnsupportedLanguageError:
                            skipped_optional_language_files.append(rel)

                for path in changed_abs_set:
                    if not path.exists():
                        try:
                            rel = path.relative_to(repo_root).as_posix()
                            if rel in stored and _is_included_rel_path(rel, include_prefixes):
                                deleted.append(rel)
                        except Exception:
                            pass
                warnings = _missing_optional_language_warnings_for_rel_paths(
                    skipped_optional_language_files
                )
            else:
                warnings = _missing_optional_language_warnings(
                    repo_root,
                    exclude_patterns=exclude_patterns,
                    include_roots=include_prefixes,
                )
                current_files = {
                    path.resolve().relative_to(repo_root).as_posix(): (parser, path)
                    for parser, path in _filter_included_files(
                        registry.iter_files(
                            repo_root,
                            exclude_patterns=exclude_patterns,
                        ),
                        repo_root,
                        include_prefixes,
                    )
                }
                stored = {
                    row["path"]: row["sha256"]
                    for row in index.conn.execute(
                        "SELECT path, sha256 FROM nodes WHERE type = 'file'",
                    )
                }
                deleted = sorted(path for path in stored if path not in current_files)
            timings_ms["detect_changes"] = _elapsed_ms(start)

            start = time.perf_counter()
            changed: List[str] = []
            parsed_changed: List[ParsedFile] = []
            for rel_path, (parser, path) in sorted(current_files.items()):
                try:
                    parsed = _parse_one_cached(parser, path, repo_root, cache)
                    if stored.get(rel_path) != parsed.sha256:
                        changed.append(rel_path)
                        parsed_changed.append(parsed)
                except ValueError:
                    pass
            timings_ms["parse_changed"] = _elapsed_ms(start)

            if not changed and not deleted:
                return RefreshResult(
                    command="refresh",
                    db_path=self.db_path,
                    repo_root=str(repo_root),
                    profile=config.name,
                    files_indexed=0,
                    symbols_indexed=0,
                    edges_indexed=0,
                    cache_hits=cache.hits,
                    cache_misses=cache.misses,
                    unchanged_files=sorted(set(stored.keys()) - set(changed) - set(deleted)),
                    warnings=warnings,
                    timings_ms=timings_ms,
                )

            start = time.perf_counter()
            impact_snapshot = _snapshot_refresh_impact(index, [*changed, *deleted])
            old_symbol_ids = [str(row["symbol_id"]) for row in impact_snapshot]
            pre_dependents: List[str] = []
            pre_dependents_cap_hit = False
            if old_symbol_ids and dependents_limit > 0:
                pre_dependents, pre_dependents_cap_hit = _find_dependent_files(
                    index,
                    old_symbol_ids,
                    set(changed) | set(deleted),
                    dependents_limit,
                )

            changed_symbols: List[str] = []
            for rel_path in deleted:
                _delete_file_impact_payload(index, rel_path)
                changed_symbols.extend(index.delete_file_payload(rel_path, remove_incoming=True))
            for rel_path in changed:
                _delete_file_impact_payload(index, rel_path)
                changed_symbols.extend(index.delete_file_payload(rel_path, remove_incoming=False))
            timings_ms["delete_old"] = _elapsed_ms(start)

            start = time.perf_counter()
            stats = _write_parsed_files(index, str(repo_root), parsed_changed)
            _finalize_symbol_history(index, impact_snapshot)
            index.cleanup_orphan_edges()
            index.cleanup_orphan_folders()
            timings_ms["write_graph"] = _elapsed_ms(start)

            changed_symbols.extend(
                symbol.node_id for parsed in parsed_changed for symbol in parsed.symbols
            )

            # --- P5-4: bounded dependent expansion ---
            start = time.perf_counter()
            dependents_expanded = 0
            dependents_cap_hit = pre_dependents_cap_hit
            if changed_symbols and dependents_limit > 0:
                dep_files = list(pre_dependents)
                remaining = max(0, dependents_limit - len(dep_files))
                if remaining and not dependents_cap_hit:
                    post_files, cap_hit = _find_dependent_files(
                        index,
                        changed_symbols,
                        set(changed) | set(deleted) | set(dep_files),
                        remaining,
                    )
                    dep_files.extend(post_files)
                    dependents_cap_hit = cap_hit
                if dep_files:
                    dep_parsed: List[ParsedFile] = []
                    for rel_path in dep_files:
                        full = repo_root / rel_path
                        if not full.exists():
                            continue
                        try:
                            parser = registry.for_extension(full.suffix)
                            parsed = _parse_one_cached(parser, full, repo_root, cache)
                            dep_parsed.append(parsed)
                        except (UnsupportedLanguageError, ValueError):
                            continue
                    if dep_parsed:
                        for parsed in dep_parsed:
                            _delete_file_impact_payload(index, parsed.rel_path)
                            index.delete_file_payload(parsed.rel_path, remove_incoming=False)
                        dep_stats = _write_parsed_files(index, str(repo_root), dep_parsed)
                        index.cleanup_orphan_edges()
                        dependents_expanded = len(dep_parsed)
                        stats["symbols"] += dep_stats["symbols"]
                        stats["edges"] = dep_stats["edges"]
            timings_ms["dependent_expansion"] = _elapsed_ms(start)

            parse_errors = {
                parsed.rel_path: parsed.parse_error or ""
                for parsed in parsed_changed
                if parsed.parse_status != "ok"
            }
            return RefreshResult(
                command="refresh",
                db_path=self.db_path,
                repo_root=str(repo_root),
                profile=config.name,
                files_indexed=len(parsed_changed),
                symbols_indexed=stats["symbols"],
                edges_indexed=stats["edges"],
                cache_hits=cache.hits,
                cache_misses=cache.misses,
                unchanged_files=sorted(set(stored.keys()) - set(changed) - set(deleted)),
                changed_files=changed,
                deleted_files=deleted,
                changed_symbols=sorted(set(changed_symbols)),
                parse_errors=parse_errors,
                warnings=warnings,
                dependents_expanded=dependents_expanded,
                dependents_cap_hit=dependents_cap_hit,
                timings_ms=timings_ms,
            )
        finally:
            index.close()
            cache.close()
            from csegraph._core.retrieval.cache import CACHE

            CACHE.clear(self.db_path)


def _migrate_supported_schema(index: ProjectIndex) -> None:
    metadata_exists = index.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'metadata'"
    ).fetchone()
    if not metadata_exists:
        return
    row = index.conn.execute(
        "SELECT value FROM metadata WHERE key = 'schema_version'"
    ).fetchone()
    if row is None or row["value"] == SCHEMA_VERSION:
        return
    migrate_schema(index.conn, row["value"])
    index.conn.commit()


def _clear_impact_index(index: ProjectIndex) -> None:
    for table_name in (
        "test_assertions",
        "edge_occurrences",
        "import_bindings",
        "symbol_history",
    ):
        index.conn.execute(f"DELETE FROM {table_name}")
    index.conn.commit()


def _snapshot_refresh_impact(
    index: ProjectIndex,
    rel_paths: Sequence[str],
) -> List[Dict[str, object]]:
    if not rel_paths:
        return []
    placeholders = ",".join("?" for _ in rel_paths)
    snapshots = [
        dict(row)
        for row in index.conn.execute(
            f"""
            SELECT
                id AS symbol_id, file_id, path, kind, name, signature,
                source_hash, start_line, end_line, metadata
            FROM symbols
            WHERE path IN ({placeholders})
            """,
            tuple(rel_paths),
        )
    ]
    if not snapshots:
        return []

    now = time.time()
    history_rows = [
        (
            row["symbol_id"],
            row["file_id"],
            row["path"],
            row["kind"],
            row["name"],
            row["signature"],
            row["source_hash"],
            row["start_line"],
            row["end_line"],
            row["metadata"],
            now,
        )
        for row in snapshots
    ]
    index.conn.executemany(
        """
        INSERT INTO symbol_history(
            symbol_id, file_id, path, kind, name, signature, source_hash,
            start_line, end_line, state, replaced_by, metadata, recorded_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'superseded', NULL, ?, ?)
        ON CONFLICT(symbol_id, source_hash) DO UPDATE SET
            state = 'superseded',
            replaced_by = NULL,
            recorded_at = excluded.recorded_at
        """,
        history_rows,
    )
    _preserve_stale_impact_evidence(
        index,
        [str(row["symbol_id"]) for row in snapshots],
    )
    index.conn.commit()
    return snapshots


def _preserve_stale_impact_evidence(
    index: ProjectIndex,
    symbol_ids: Sequence[str],
) -> None:
    if not symbol_ids:
        return
    placeholders = ",".join("?" for _ in symbol_ids)
    params = tuple(symbol_ids)
    index.conn.execute(
        f"""
        UPDATE OR REPLACE edge_occurrences
        SET is_stale = 1,
            resolution_status = 'stale_target',
            resolution_strategy = 'refresh_snapshot'
        WHERE target IN ({placeholders})
        """,
        params,
    )

    rows = index.conn.execute(
        f"""
        SELECT
            COALESCE(r.enclosing_symbol_id, r.source_file_id) AS occurrence_source,
            r.target,
            r.kind,
            r.source_file_id,
            r.enclosing_symbol_id,
            r.name,
            r.start_line,
            r.end_line,
            r.source AS source_text,
            r.metadata
        FROM symbol_references r
        WHERE r.target IN ({placeholders})
          AND r.kind IN ('calls', 'inherits', 'decorates', 'tested_by')
        """,
        params,
    ).fetchall()
    if not rows:
        return
    index.conn.executemany(
        """
        INSERT OR REPLACE INTO edge_occurrences(
            source, target, relation, source_file_id, enclosing_symbol_id,
            name, start_line, end_line, source_text, resolution_status,
            resolution_strategy, candidate_targets, is_stale, metadata
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'stale_target',
               'refresh_snapshot', ?, 1, ?)
        """,
        [
            (
                row["occurrence_source"],
                row["target"],
                row["kind"],
                row["source_file_id"],
                row["enclosing_symbol_id"],
                row["name"],
                row["start_line"],
                row["end_line"],
                row["source_text"],
                json.dumps([row["target"]]),
                json_dumps(
                    {
                        "stale_reason": "target_changed_or_deleted",
                        "previous_metadata": row["metadata"],
                    }
                ),
            )
            for row in rows
        ],
    )


def _delete_file_impact_payload(index: ProjectIndex, rel_path: str) -> None:
    file_id = file_node_id(rel_path)
    index.conn.execute("DELETE FROM import_bindings WHERE file_id = ?", (file_id,))
    index.conn.execute("DELETE FROM test_assertions WHERE source_file_id = ?", (file_id,))
    index.conn.execute(
        "DELETE FROM edge_occurrences WHERE source_file_id = ? AND is_stale = 0",
        (file_id,),
    )


def _finalize_symbol_history(
    index: ProjectIndex,
    snapshots: Sequence[Dict[str, object]],
) -> None:
    for snapshot in snapshots:
        symbol_id = str(snapshot["symbol_id"])
        source_hash = str(snapshot["source_hash"])
        raw_start_line = snapshot.get("start_line")
        raw_end_line = snapshot.get("end_line")
        start_line = raw_start_line if isinstance(raw_start_line, int) else 0
        end_line = raw_end_line if isinstance(raw_end_line, int) else start_line
        current = index.conn.execute(
            "SELECT source_hash FROM symbols WHERE id = ?",
            (symbol_id,),
        ).fetchone()
        if current is not None:
            if current["source_hash"] == source_hash:
                index.conn.execute(
                    """
                    UPDATE symbol_history
                    SET state = 'active', replaced_by = NULL
                    WHERE symbol_id = ? AND source_hash = ?
                    """,
                    (symbol_id, source_hash),
                )
            continue

        replacement = index.conn.execute(
            """
            SELECT id
            FROM symbols
            WHERE path = ? AND kind = ?
              AND COALESCE(end_line, start_line, 0) >= ?
              AND COALESCE(start_line, end_line, 0) <= ?
            ORDER BY ABS(COALESCE(start_line, 0) - ?), id
            LIMIT 1
            """,
            (
                snapshot["path"],
                snapshot["kind"],
                max(0, start_line - 1),
                end_line + 1,
                start_line,
            ),
        ).fetchone()
        index.conn.execute(
            """
            UPDATE symbol_history
            SET state = 'tombstone', replaced_by = ?
            WHERE symbol_id = ? AND source_hash = ?
            """,
            (replacement["id"] if replacement else None, symbol_id, source_hash),
        )
    index.conn.commit()


def _find_dependent_files(
    index: ProjectIndex,
    changed_symbol_ids: List[str],
    already_processed: set,
    limit: int,
) -> tuple:
    """Find files containing symbols that directly depend on changed symbols.

    Returns (dep_file_paths, cap_hit) where cap_hit is True if the limit was reached.
    """
    if not changed_symbol_ids:
        return [], False

    placeholders = ",".join("?" for _ in changed_symbol_ids)
    rows = index.conn.execute(
        f"""
        SELECT DISTINCT n.path
        FROM edges e
        JOIN nodes n ON n.id = CASE
            WHEN e.target IN ({placeholders}) THEN e.source
            WHEN e.source IN ({placeholders}) AND e.relation = 'tested_by' THEN e.target
        END
        WHERE (
              (
                e.target IN ({placeholders})
                AND e.relation IN ('calls', 'imports', 'inherits', 'decorates')
              )
              OR (
                e.source IN ({placeholders})
                AND e.relation = 'tested_by'
              )
            )
          AND n.type IN ('file', 'class', 'function', 'method', 'test', 'document')
          AND n.path IS NOT NULL
        LIMIT ?
        """,
        (
            *changed_symbol_ids,
            *changed_symbol_ids,
            *changed_symbol_ids,
            *changed_symbol_ids,
            limit + 1,
        ),
    ).fetchall()

    dep_paths = []
    for row in rows:
        path = row["path"]
        if path and path not in already_processed:
            dep_paths.append(path)

    cap_hit = len(rows) > limit
    return dep_paths[:limit], cap_hit


def _resolve_cross_file_methods(index: ProjectIndex) -> None:
    # 1. Update nodes table to link methods to classes across files.
    #    Uses a window function instead of a correlated subquery with ORDER BY
    #    because SQLite ≥3.45 cannot resolve outer-table columns inside
    #    ORDER BY of a correlated subquery against the same table.
    index.conn.execute(
        """
        WITH methods AS (
            SELECT id, name, path
            FROM nodes
            WHERE type = 'method'
              AND parent_id LIKE 'file::%'
              AND INSTR(name, '.') > 1
        ),
        candidates AS (
            SELECT
                m.id AS method_id,
                c.id AS class_id,
                ROW_NUMBER() OVER (
                    PARTITION BY m.id
                    ORDER BY (SUBSTR(c.path, 1, INSTR(c.path, '/') - 1)
                            = SUBSTR(m.path, 1, INSTR(m.path, '/') - 1)) DESC,
                             c.id ASC
                ) AS rn
            FROM methods m
            JOIN nodes c ON c.type = 'class'
                AND c.name = SUBSTR(m.name, 1, INSTR(m.name, '.') - 1)
        )
        UPDATE nodes
        SET parent_id = (SELECT class_id FROM candidates WHERE method_id = nodes.id AND rn = 1)
        WHERE id IN (SELECT method_id FROM candidates WHERE rn = 1)
        """
    )
    # 2. Update edges table to point 'contains' source to the class instead of the file
    index.conn.execute(
        """
        UPDATE edges
        SET source = (
            SELECT parent_id FROM nodes
            WHERE nodes.id = edges.target
        )
        WHERE edges.relation = 'contains'
          AND edges.target IN (
              SELECT id FROM nodes
              WHERE type = 'method'
                AND NOT parent_id LIKE 'file::%'
          )
          AND edges.source LIKE 'file::%'
        """
    )


def _write_parsed_files(
    index: ProjectIndex,
    repo_root: str,
    parsed_files: Sequence[ParsedFile],
) -> Dict[str, int]:
    _validate_parsed_languages(parsed_files)
    now = time.time()
    structural_edges = _write_repo_and_folders(index, repo_root, parsed_files, now)
    batch = _WriteBatch()

    _insert_file_nodes(index, repo_root, parsed_files, now, batch)
    _load_symbol_lookup(index, batch)
    _insert_symbol_nodes(index, parsed_files, now, batch)
    _flush_summary_and_lexical_rows(index, batch)
    _insert_edges(index, parsed_files, structural_edges, batch)
    _resolve_cross_file_methods(index)
    index.conn.commit()

    total_edges = int(index.conn.execute("SELECT COUNT(*) AS c FROM edges").fetchone()["c"])
    return {
        "symbols": sum(len(parsed.symbols) for parsed in parsed_files),
        "edges": total_edges,
    }


def _validate_parsed_languages(parsed_files: Sequence[ParsedFile]) -> None:
    for parsed in parsed_files:
        if not isinstance(parsed.language, str) or not parsed.language:
            raise ValueError(f"language is required for file {parsed.rel_path!r}")


def _insert_file_nodes(
    index: ProjectIndex,
    repo_root: str,
    parsed_files: Sequence[ParsedFile],
    now: float,
    batch: _WriteBatch,
) -> None:
    file_node_rows: List[tuple] = []
    file_rows: List[tuple] = []
    for parsed in parsed_files:
        node_id = file_node_id(parsed.rel_path)
        parent_dir = "/".join(parsed.rel_path.split("/")[:-1])
        parent = (
            folder_node_id(parent_dir)
            if parent_dir
            else repo_node_id(Path(repo_root).name or "repo")
        )
        file_meta = json.dumps({"size": parsed.size, "mtime": parsed.mtime}, sort_keys=True)
        file_node_rows.append(
            (
                node_id,
                parent,
                Path(parsed.rel_path).name,
                parsed.rel_path,
                parsed.language,
                parsed.sha256,
                parsed.sha256,
                parsed.parse_status,
                parsed.parse_error,
                file_meta,
                now,
            )
        )
        file_rows.append(
            (
                node_id,
                parsed.rel_path,
                Path(parsed.rel_path).name,
                parsed.language,
                parsed.sha256,
                parsed.sha256,
                parsed.parse_status,
                parsed.parse_error,
                parsed.size,
                parsed.mtime,
                file_meta,
                now,
            )
        )
        file_summary = _file_summary(parsed)
        batch.summary_rows.append((node_id, parsed.sha256, file_summary, "file", now))
        batch.lexical_delete_ids.append((node_id,))
        batch.lexical_rows.append(
            (node_id, Path(parsed.rel_path).name, parsed.rel_path, "", "", file_summary, "")
        )

    if file_node_rows:
        index.conn.executemany(
            """
            INSERT INTO nodes(
                id, parent_id, type, name, path,
                language, sha256, signature, docstring,
                start_line, end_line, source_hash,
                parse_status, parse_error, metadata, is_test, updated_at
            ) VALUES(?, ?, 'file', ?, ?, ?, ?, NULL, NULL,
                     NULL, NULL, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(id) DO UPDATE SET
                parent_id = excluded.parent_id,
                language = excluded.language,
                sha256 = excluded.sha256,
                source_hash = excluded.source_hash,
                parse_status = excluded.parse_status,
                parse_error = excluded.parse_error,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            file_node_rows,
        )
    if file_rows:
        index.conn.executemany(
            """
            INSERT INTO files(
                id, path, name, language, sha256, source_hash,
                parse_status, parse_error, size, mtime, metadata, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                path = excluded.path,
                name = excluded.name,
                language = excluded.language,
                sha256 = excluded.sha256,
                source_hash = excluded.source_hash,
                parse_status = excluded.parse_status,
                parse_error = excluded.parse_error,
                size = excluded.size,
                mtime = excluded.mtime,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            file_rows,
        )


def _load_symbol_lookup(index: ProjectIndex, batch: _WriteBatch) -> None:
    for row in index.conn.execute(
        """
        SELECT id, name, path, type FROM nodes
        WHERE type IN ('class','function','method','document')
        """,
    ):
        batch.symbol_by_name[row["name"]].append(row["id"])
        if "." in row["name"]:
            batch.symbol_by_name[row["name"].split(".")[-1]].append(row["id"])
        batch.node_to_file_node[row["id"]] = file_node_id(row["path"])
        batch.node_kind_by_id[row["id"]] = row["type"]


def _insert_symbol_nodes(
    index: ProjectIndex,
    parsed_files: Sequence[ParsedFile],
    now: float,
    batch: _WriteBatch,
) -> None:
    symbol_node_rows: List[tuple] = []
    symbol_rows: List[tuple] = []
    history_rows: List[tuple] = []
    for parsed in parsed_files:
        tokenizer = registry.tokenizer_for(parsed.language)
        for symbol in parsed.symbols:
            parent = symbol.parent_symbol_id or file_node_id(parsed.rel_path)
            metadata = json.dumps(
                {"is_test": symbol.is_test, "bases": symbol.bases, "decorators": symbol.decorators},
                sort_keys=True,
            )
            symbol_node_rows.append(
                (
                    symbol.node_id,
                    parent,
                    symbol.kind,
                    symbol.name,
                    parsed.rel_path,
                    parsed.language,
                    symbol.signature,
                    symbol.docstring,
                    symbol.start_line,
                    symbol.end_line,
                    symbol.source_hash,
                    metadata,
                    1 if symbol.is_test else 0,
                    now,
                )
            )
            symbol_rows.append(
                (
                    symbol.node_id,
                    file_node_id(parsed.rel_path),
                    symbol.parent_symbol_id,
                    symbol.kind,
                    symbol.name,
                    parsed.rel_path,
                    parsed.language,
                    symbol.signature,
                    symbol.docstring,
                    symbol.start_line,
                    symbol.end_line,
                    symbol.source_hash,
                    metadata,
                    1 if symbol.is_test else 0,
                    now,
                )
            )
            history_rows.append(
                (
                    symbol.node_id,
                    file_node_id(parsed.rel_path),
                    parsed.rel_path,
                    symbol.kind,
                    symbol.name,
                    symbol.signature,
                    symbol.source_hash,
                    symbol.start_line,
                    symbol.end_line,
                    metadata,
                    now,
                )
            )
            batch.symbol_by_name[symbol.name].append(symbol.node_id)
            if "." in symbol.name:
                batch.symbol_by_name[symbol.name.split(".")[-1]].append(symbol.node_id)
            batch.node_to_file_node[symbol.node_id] = file_node_id(parsed.rel_path)
            batch.node_kind_by_id[symbol.node_id] = symbol.kind

            summary = _symbol_summary(symbol)
            batch.summary_rows.append(
                (symbol.node_id, symbol.source_hash, summary, symbol.kind, now)
            )
            batch.lexical_delete_ids.append((symbol.node_id,))
            batch.lexical_rows.append(
                (
                    symbol.node_id,
                    symbol.name,
                    parsed.rel_path,
                    symbol.signature or "",
                    symbol.docstring or "",
                    summary,
                    " ".join(tokenizer.tokenize(symbol.source)),
                )
            )

    if symbol_node_rows:
        index.conn.executemany(
            """
            INSERT INTO nodes(
                id, parent_id, type, name, path,
                language, sha256, signature, docstring,
                start_line, end_line, source_hash,
                parse_status, parse_error, metadata, is_test, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                parent_id = excluded.parent_id,
                type = excluded.type,
                language = excluded.language,
                signature = excluded.signature,
                docstring = excluded.docstring,
                start_line = excluded.start_line,
                end_line = excluded.end_line,
                source_hash = excluded.source_hash,
                metadata = excluded.metadata,
                is_test = excluded.is_test,
                updated_at = excluded.updated_at
            """,
            symbol_node_rows,
        )
    if symbol_rows:
        index.conn.executemany(
            """
            INSERT INTO symbols(
                id, file_id, parent_id, kind, name, path, language,
                signature, docstring, start_line, end_line, source_hash,
                metadata, is_test, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                file_id = excluded.file_id,
                parent_id = excluded.parent_id,
                kind = excluded.kind,
                name = excluded.name,
                path = excluded.path,
                language = excluded.language,
                signature = excluded.signature,
                docstring = excluded.docstring,
                start_line = excluded.start_line,
                end_line = excluded.end_line,
                source_hash = excluded.source_hash,
                metadata = excluded.metadata,
                is_test = excluded.is_test,
                updated_at = excluded.updated_at
            """,
            symbol_rows,
        )
    if history_rows:
        index.conn.executemany(
            """
            INSERT INTO symbol_history(
                symbol_id, file_id, path, kind, name, signature, source_hash,
                start_line, end_line, state, replaced_by, metadata, recorded_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, ?, ?)
            ON CONFLICT(symbol_id, source_hash) DO UPDATE SET
                file_id = excluded.file_id,
                path = excluded.path,
                kind = excluded.kind,
                name = excluded.name,
                signature = excluded.signature,
                start_line = excluded.start_line,
                end_line = excluded.end_line,
                state = 'active',
                replaced_by = NULL,
                metadata = excluded.metadata,
                recorded_at = excluded.recorded_at
            """,
            history_rows,
        )


def _flush_summary_and_lexical_rows(index: ProjectIndex, batch: _WriteBatch) -> None:
    if batch.summary_rows:
        index.conn.executemany(
            """
            INSERT INTO summaries(node_id, source_hash, summary, kind, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                source_hash = excluded.source_hash,
                summary = excluded.summary,
                kind = excluded.kind,
                updated_at = excluded.updated_at
            """,
            batch.summary_rows,
        )

    if batch.lexical_delete_ids:
        index.conn.executemany(
            "DELETE FROM lexical_index WHERE node_id = ?",
            batch.lexical_delete_ids,
        )
    if batch.lexical_rows:
        index.conn.executemany(
            """
            INSERT INTO lexical_index(node_id, name, path, signature, docstring, summary, source)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            batch.lexical_rows,
        )


def _insert_edges(
    index: ProjectIndex,
    parsed_files: Sequence[ParsedFile],
    structural_edges: Sequence[tuple],
    batch: _WriteBatch,
) -> None:
    module_to_file_id = _module_to_file_id(index)
    edge_rows: List[tuple] = list(structural_edges)
    import_rows: List[tuple] = []
    import_binding_rows: List[tuple] = []
    reference_rows: List[tuple] = []
    occurrence_rows: List[tuple] = []
    assertion_rows: List[tuple] = []
    for parsed in parsed_files:
        try:
            parser = registry.for_extension(Path(parsed.rel_path).suffix)
        except UnsupportedLanguageError:
            continue
        current_module = parser.module_name_from_relpath(parsed.rel_path)
        current_file_id = file_node_id(parsed.rel_path)
        for symbol in parsed.symbols:
            source = (
                symbol.parent_symbol_id
                if symbol.kind == "method" and symbol.parent_symbol_id
                else current_file_id
            )
            edge_rows.append(_edge(source, symbol.node_id, "contains"))

        bindings_by_local: Dict[str, List[_ImportBinding]] = defaultdict(list)
        import_records = _normalized_import_records(parsed)
        for import_record in import_records:
            import_name = import_record.name
            target_file_id = parser.resolve_local_import(
                import_name, module_to_file_id, current_module
            )
            import_record.resolved_file_id = target_file_id
            import_rows.append(
                (
                    current_file_id,
                    parsed.rel_path,
                    parsed.language,
                    import_name,
                    target_file_id,
                    import_record.start_line,
                    import_record.end_line,
                    import_record.source,
                    json_dumps(import_record.metadata),
                )
            )
            bindings = _import_bindings_for(
                import_record,
                target_file_id,
                batch,
            )
            for binding in bindings:
                bindings_by_local[binding.local_name].append(binding)
                import_binding_rows.append(
                    (
                        current_file_id,
                        binding.import_name,
                        binding.local_name,
                        binding.imported_name,
                        binding.qualified_name,
                        binding.binding_kind,
                        binding.resolved_file_id,
                        binding.resolved_symbol_id,
                        binding.resolution_status,
                        binding.start_line,
                        binding.end_line,
                        binding.source,
                        json_dumps(binding.metadata),
                    )
                )
            import_status = "resolved" if target_file_id else "external"
            occurrence_rows.append(
                _edge_occurrence(
                    current_file_id,
                    target_file_id,
                    "imports",
                    current_file_id,
                    None,
                    import_name,
                    import_record.start_line,
                    import_record.end_line,
                    import_record.source,
                    _TargetResolution(
                        target_file_id,
                        import_status,
                        "local_module" if target_file_id else "external_module",
                        (target_file_id,) if target_file_id else (),
                    ),
                    import_record.metadata,
                )
            )
            if target_file_id:
                edge_rows.append(
                    _edge(
                        current_file_id,
                        target_file_id,
                        "imports",
                        {
                            "import": import_name,
                            "start_line": import_record.start_line,
                            "end_line": import_record.end_line,
                            "source": import_record.source,
                        },
                    )
                )

        _SYMBOL_EDGE_SPECS = [
            ("calls", "calls", "symbol", False),
            ("bases", "inherits", "base", False),
            ("decorators", "decorates", "decorator", True),
        ]
        for symbol in parsed.symbols:
            call_resolutions: Dict[str, _TargetResolution] = {}
            for attr, relation, meta_key, reverse in _SYMBOL_EDGE_SPECS:
                for name in getattr(symbol, attr):
                    resolution = _resolve_call_target(
                        name,
                        current_file_id,
                        batch.symbol_by_name,
                        batch.node_to_file_node,
                        batch.node_kind_by_id,
                        bindings_by_local,
                    )
                    if relation == "calls":
                        call_resolutions[name] = resolution
                    references = _references_for(symbol, relation, name)
                    target = resolution.target
                    if target and target != symbol.node_id:
                        src, tgt = (target, symbol.node_id) if reverse else (symbol.node_id, target)
                        first_reference = references[0]
                        edge_rows.append(
                            _edge(
                                src,
                                tgt,
                                relation,
                                {
                                    meta_key: name,
                                    "start_line": first_reference.start_line,
                                    "end_line": first_reference.end_line,
                                    "source": first_reference.source,
                                    "resolution_strategy": resolution.strategy,
                                },
                            )
                        )
                    for reference in references:
                        evidence_metadata = {
                            **reference.metadata,
                            "resolution_status": resolution.status,
                            "resolution_strategy": resolution.strategy,
                            "candidate_targets": list(resolution.candidates),
                        }
                        reference_rows.append(
                            _reference(
                                current_file_id,
                                symbol.node_id,
                                target,
                                relation,
                                name,
                                reference.start_line,
                                reference.end_line,
                                reference.source,
                                evidence_metadata,
                            )
                        )
                        occurrence_rows.append(
                            _edge_occurrence(
                                symbol.node_id,
                                target,
                                relation,
                                current_file_id,
                                symbol.node_id,
                                name,
                                reference.start_line,
                                reference.end_line,
                                reference.source,
                                resolution,
                                reference.metadata,
                            )
                        )
            if symbol.is_test:
                for call in symbol.calls:
                    resolution = call_resolutions.get(call) or _resolve_call_target(
                        call,
                        current_file_id,
                        batch.symbol_by_name,
                        batch.node_to_file_node,
                        batch.node_kind_by_id,
                        bindings_by_local,
                    )
                    target = resolution.target
                    references = _test_references_for(symbol, call)
                    if target and target != symbol.node_id:
                        first_reference = references[0]
                        edge_rows.append(
                            _edge(
                                target,
                                symbol.node_id,
                                "tested_by",
                                {
                                    "via": call,
                                    "start_line": first_reference.start_line,
                                    "end_line": first_reference.end_line,
                                    "source": first_reference.source,
                                    "resolution_strategy": resolution.strategy,
                                },
                            )
                        )
                    for reference in references:
                        evidence_metadata = {
                            **reference.metadata,
                            "resolution_status": resolution.status,
                            "resolution_strategy": resolution.strategy,
                            "candidate_targets": list(resolution.candidates),
                        }
                        reference_rows.append(
                            _reference(
                                current_file_id,
                                symbol.node_id,
                                target,
                                "tested_by",
                                call,
                                reference.start_line,
                                reference.end_line,
                                reference.source,
                                evidence_metadata,
                            )
                        )
                        occurrence_rows.append(
                            _edge_occurrence(
                                symbol.node_id,
                                target,
                                "tested_by",
                                current_file_id,
                                symbol.node_id,
                                call,
                                reference.start_line,
                                reference.end_line,
                                reference.source,
                                resolution,
                                reference.metadata,
                            )
                        )

                for assertion in _test_assertion_evidence(symbol):
                    if not assertion.call_names:
                        assertion_rows.append(
                            _test_assertion_row(
                                symbol,
                                current_file_id,
                                assertion,
                                None,
                                _TargetResolution(None, "unresolved", "no_call"),
                                None,
                            )
                        )
                    for call_name in assertion.call_names:
                        resolution = call_resolutions.get(call_name) or _resolve_call_target(
                            call_name,
                            current_file_id,
                            batch.symbol_by_name,
                            batch.node_to_file_node,
                            batch.node_kind_by_id,
                            bindings_by_local,
                        )
                        assertion_rows.append(
                            _test_assertion_row(
                                symbol,
                                current_file_id,
                                assertion,
                                resolution.target,
                                resolution,
                                call_name,
                            )
                        )

    if edge_rows:
        index.conn.executemany(
            """
            INSERT OR IGNORE INTO edges(source, target, relation, metadata, confidence, confidence_tier)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            edge_rows,
        )
        index.conn.executemany(
            """
            INSERT OR IGNORE INTO relationships(source, target, kind, metadata, confidence, confidence_tier)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            edge_rows,
        )
    if import_rows:
        index.conn.executemany(
            """
            INSERT OR IGNORE INTO imports(
                file_id, path, language, import_name, resolved_file_id,
                start_line, end_line, source, metadata
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            import_rows,
        )
    if import_binding_rows:
        index.conn.executemany(
            """
            INSERT OR REPLACE INTO import_bindings(
                file_id, import_name, local_name, imported_name, qualified_name,
                binding_kind, resolved_file_id, resolved_symbol_id, resolution_status,
                start_line, end_line, source, metadata
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            import_binding_rows,
        )
    if reference_rows:
        index.conn.executemany(
            """
            INSERT INTO symbol_references(
                source_file_id, enclosing_symbol_id, target, kind, name,
                start_line, end_line, source, metadata
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            reference_rows,
        )
    if occurrence_rows:
        index.conn.executemany(
            """
            INSERT OR REPLACE INTO edge_occurrences(
                source, target, relation, source_file_id, enclosing_symbol_id,
                name, start_line, end_line, source_text, resolution_status,
                resolution_strategy, candidate_targets, is_stale, metadata
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            occurrence_rows,
        )
    if assertion_rows:
        index.conn.executemany(
            """
            INSERT OR REPLACE INTO test_assertions(
                test_symbol_id, source_file_id, target_symbol_id, assertion_kind,
                expression, start_line, end_line, resolution_status,
                candidate_targets, metadata
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            assertion_rows,
        )


def _normalized_import_records(parsed: ParsedFile) -> List[ParsedImport]:
    if parsed.import_records:
        return parsed.import_records
    return [
        ParsedImport(
            name=name,
            start_line=1,
            end_line=1,
            source="",
        )
        for name in parsed.imports
    ]


def _import_bindings_for(
    import_record: ParsedImport,
    resolved_file_id: Optional[str],
    batch: _WriteBatch,
) -> List[_ImportBinding]:
    metadata = import_record.metadata
    raw_bindings = metadata.get("imports")
    binding_specs: List[Dict[str, object]] = []
    if isinstance(raw_bindings, list):
        binding_specs.extend(item for item in raw_bindings if isinstance(item, dict))

    if not binding_specs:
        imported_name = str(
            metadata.get("imported_name") or _symbol_name_from_import_target(import_record.name)
        )
        local_name = str(metadata.get("local_name") or imported_name)
        binding_specs.append(
            {
                "name": imported_name,
                "local": local_name,
                "qualified": metadata.get("module") or import_record.name,
            }
        )

    bindings: List[_ImportBinding] = []
    for spec in binding_specs:
        imported_name = str(spec.get("name") or metadata.get("imported_name") or "")
        local_name = str(spec.get("local") or imported_name)
        qualified_name = spec.get("qualified")
        if not isinstance(qualified_name, str):
            module = metadata.get("module")
            if isinstance(module, str) and imported_name not in {"", "*", "default"}:
                qualified_name = f"{module}.{imported_name}"
            elif isinstance(module, str):
                qualified_name = module
            else:
                qualified_name = None

        if imported_name == "*" or metadata.get("namespace") == local_name:
            binding_kind = "namespace"
        elif imported_name == "default":
            binding_kind = "default"
        elif metadata.get("style") in {"import", "require"}:
            binding_kind = "module"
        else:
            binding_kind = "named"

        candidates = _candidates_in_file(
            imported_name,
            resolved_file_id,
            batch.symbol_by_name,
            batch.node_to_file_node,
        )
        if len(candidates) == 1:
            resolved_symbol_id = candidates[0]
            status = "resolved"
        elif len(candidates) > 1:
            resolved_symbol_id = None
            status = "ambiguous"
        elif resolved_file_id:
            resolved_symbol_id = None
            status = "file_resolved"
        else:
            resolved_symbol_id = None
            status = "external"

        bindings.append(
            _ImportBinding(
                import_name=import_record.name,
                local_name=local_name,
                imported_name=imported_name,
                qualified_name=qualified_name,
                binding_kind=binding_kind,
                resolved_file_id=resolved_file_id,
                resolved_symbol_id=resolved_symbol_id,
                resolution_status=status,
                start_line=import_record.start_line,
                end_line=import_record.end_line,
                source=import_record.source,
                metadata={
                    "candidate_targets": candidates,
                    "type_only": bool(spec.get("type_only") or metadata.get("type_only")),
                },
            )
        )
    return bindings


def _symbol_name_from_import_target(target: str) -> str:
    return target.rsplit(".", 1)[-1].rsplit("/", 1)[-1]


def _references_for(
    symbol: ParsedSymbol,
    kind: str,
    name: str,
) -> List[ParsedReference]:
    matches = [
        reference
        for reference in symbol.references
        if reference.kind == kind and reference.name == name
    ]
    if matches:
        return matches
    return [
        ParsedReference(
            kind=kind,
            name=name,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            source="",
        )
    ]


def _test_references_for(symbol: ParsedSymbol, name: str) -> List[ParsedReference]:
    matches = [
        reference
        for reference in symbol.references
        if reference.kind in {"test", "calls"} and reference.name == name
    ]
    if matches:
        return matches
    return [
        ParsedReference(
            kind="test",
            name=name,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            source="",
        )
    ]


def _reference(
    source_file_id: str,
    enclosing_symbol_id: str,
    target: Optional[str],
    kind: str,
    name: str,
    start_line: int,
    end_line: int,
    source: str,
    metadata: Dict[str, object],
) -> tuple:
    return (
        source_file_id,
        enclosing_symbol_id,
        target,
        kind,
        name,
        start_line,
        end_line,
        source,
        json_dumps(metadata),
    )


def _edge_occurrence(
    source: str,
    target: Optional[str],
    relation: str,
    source_file_id: str,
    enclosing_symbol_id: Optional[str],
    name: str,
    start_line: int,
    end_line: int,
    source_text: str,
    resolution: _TargetResolution,
    metadata: Optional[Dict[str, object]] = None,
) -> tuple:
    return (
        source,
        target,
        relation,
        source_file_id,
        enclosing_symbol_id,
        name,
        start_line,
        end_line,
        source_text,
        resolution.status,
        resolution.strategy,
        json.dumps(list(resolution.candidates), sort_keys=True),
        0,
        json_dumps(metadata),
    )


def _test_assertion_row(
    symbol: ParsedSymbol,
    current_file_id: str,
    assertion: _AssertionEvidence,
    target: Optional[str],
    resolution: _TargetResolution,
    call_name: Optional[str],
) -> tuple:
    return (
        symbol.node_id,
        current_file_id,
        target,
        assertion.kind,
        assertion.expression,
        assertion.start_line,
        assertion.end_line,
        resolution.status,
        json.dumps(list(resolution.candidates), sort_keys=True),
        json_dumps(
            {
                "call_name": call_name,
                "resolution_strategy": resolution.strategy,
            }
        ),
    )


def _test_assertion_evidence(symbol: ParsedSymbol) -> List[_AssertionEvidence]:
    if not symbol.source.strip():
        return []
    source = textwrap.dedent(symbol.source)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    evidence: List[_AssertionEvidence] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        calls = sorted(
            {
                call_name
                for child in ast.walk(node.test)
                if isinstance(child, ast.Call)
                for call_name in [_python_call_name(child.func)]
                if call_name
            }
        )
        expression = ast.get_source_segment(source, node) or "assert"
        evidence.append(
            _AssertionEvidence(
                kind="assert",
                expression=expression.strip(),
                start_line=symbol.start_line + node.lineno - 1,
                end_line=symbol.start_line + getattr(node, "end_lineno", node.lineno) - 1,
                call_names=tuple(calls),
            )
        )
    return sorted(evidence, key=lambda item: (item.start_line, item.end_line, item.expression))


def _python_call_name(node: ast.expr) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _parse_with_cache(file_iter, repo_root: Path, cache: ExtractionCache) -> List[ParsedFile]:
    results: List[ParsedFile] = []
    for parser, path in file_iter:
        try:
            results.append(_parse_one_cached(parser, path, repo_root, cache))
        except ValueError:
            pass
    return results


def _parse_one_cached(parser, path: Path, repo_root: Path, cache: ExtractionCache) -> ParsedFile:
    from csegraph._core.languages.base import sha256_text

    resolved_path = path.resolve()
    resolved_root = Path(repo_root).resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(
            f"Path '{path}' resolves to '{resolved_path}', which is outside repository root '{resolved_root}'"
        )
    source = resolved_path.read_text(encoding="utf-8")
    sha = sha256_text(source)
    rel = resolved_path.relative_to(resolved_root).as_posix()

    cached = cache.get(rel, sha)
    if cached is not None:
        return cached

    parsed = parser.parse(path, repo_root)
    cache.put(parsed)
    return parsed


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def _write_repo_and_folders(
    index: ProjectIndex,
    repo_root: str,
    parsed_files: Sequence[ParsedFile],
    now: float,
) -> List[tuple]:
    repo_name = Path(repo_root).name or "repo"
    repo_id = repo_node_id(repo_name)
    index.conn.execute(
        """
        INSERT INTO nodes(
            id, parent_id, type, name, path,
            language, sha256, signature, docstring,
            start_line, end_line, source_hash,
            parse_status, parse_error, metadata, updated_at
        ) VALUES(?, NULL, 'repo', ?, '', ?, NULL, NULL, NULL,
                 NULL, NULL, '', NULL, NULL, NULL, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            language = excluded.language,
            updated_at = excluded.updated_at
        """,
        (repo_id, repo_name, _STRUCTURAL_LANGUAGE, now),
    )

    folder_paths: set[str] = set()
    for parsed in parsed_files:
        parts = parsed.rel_path.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            folder_paths.add("/".join(parts[:i]))

    edges: List[tuple] = []
    for rel_dir in sorted(folder_paths, key=lambda p: p.count("/")):
        parent_parts = rel_dir.split("/")[:-1]
        parent = folder_node_id("/".join(parent_parts)) if parent_parts else repo_id
        folder_id = folder_node_id(rel_dir)
        index.conn.execute(
            """
            INSERT INTO nodes(
                id, parent_id, type, name, path,
                language, sha256, signature, docstring,
                start_line, end_line, source_hash,
                parse_status, parse_error, metadata, updated_at
            ) VALUES(?, ?, 'folder', ?, ?, ?, NULL, NULL, NULL,
                     NULL, NULL, '', NULL, NULL, NULL, ?)
            ON CONFLICT(id) DO UPDATE SET
                parent_id = excluded.parent_id,
                name = excluded.name,
                path = excluded.path,
                language = excluded.language,
                updated_at = excluded.updated_at
            """,
            (
                folder_id,
                parent,
                rel_dir.rsplit("/", 1)[-1],
                rel_dir,
                _STRUCTURAL_LANGUAGE,
                now,
            ),
        )
        edges.append(_edge(parent, folder_id, "contains"))

    for parsed in parsed_files:
        parent_dir = "/".join(parsed.rel_path.split("/")[:-1])
        parent = folder_node_id(parent_dir) if parent_dir else repo_id
        edges.append(_edge(parent, file_node_id(parsed.rel_path), "contains"))

    return edges


def _file_summary(parsed: ParsedFile) -> str:
    names = ", ".join(symbol.name for symbol in parsed.symbols[:8]) or "no symbols"
    return f"Module {parsed.rel_path} defines {names}."


def _symbol_summary(symbol: ParsedSymbol) -> str:
    parts = [symbol.signature or f"{symbol.kind} {symbol.name}"]
    if symbol.docstring:
        parts.append(symbol.docstring.split(".")[0].replace("\n", " ").strip())
    if symbol.bases:
        parts.append("inherits " + ", ".join(symbol.bases[:4]))
    if symbol.calls:
        parts.append("calls " + ", ".join(symbol.calls[:8]))
    return " - ".join(part for part in parts if part)


def _module_to_file_id(index: ProjectIndex) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for row in index.conn.execute("SELECT path FROM nodes WHERE type = 'file'"):
        path = row["path"]
        try:
            parser = registry.for_extension(Path(path).suffix)
        except UnsupportedLanguageError:
            continue
        module_name = parser.module_name_from_relpath(path)
        if module_name is not None:
            mapping[module_name] = file_node_id(path)
    return mapping


def _pick_call_target(
    symbol: str,
    current_file_id: str,
    symbol_by_name: Dict[str, List[str]],
    node_to_file_node: Dict[str, str],
    node_kind_by_id: Dict[str, str],
    preferred_file_ids: Sequence[str] | None = None,
) -> Optional[str]:
    del node_kind_by_id
    candidates = sorted(set(symbol_by_name.get(symbol, [])))
    local = [
        node_id for node_id in candidates if node_to_file_node.get(node_id) == current_file_id
    ]
    if len(local) == 1:
        return local[0]

    preferred = set(preferred_file_ids or ())
    explicitly_imported = [
        node_id for node_id in candidates if node_to_file_node.get(node_id) in preferred
    ]
    if len(explicitly_imported) == 1:
        return explicitly_imported[0]
    return None


def _resolve_call_target(
    symbol: str,
    current_file_id: str,
    symbol_by_name: Dict[str, List[str]],
    node_to_file_node: Dict[str, str],
    node_kind_by_id: Dict[str, str],
    bindings_by_local: Dict[str, List[_ImportBinding]],
) -> _TargetResolution:
    del node_kind_by_id
    candidates = sorted(set(symbol_by_name.get(symbol, [])))
    local = tuple(
        node_id
        for node_id in candidates
        if node_to_file_node.get(node_id) == current_file_id
    )
    if len(local) == 1:
        return _TargetResolution(local[0], "resolved", "same_file", local)
    if len(local) > 1:
        return _TargetResolution(None, "ambiguous", "same_file", local)

    bound_candidates: List[str] = []
    for binding in bindings_by_local.get(symbol, []):
        if binding.resolved_symbol_id:
            bound_candidates.append(binding.resolved_symbol_id)
            continue
        bound_candidates.extend(
            _candidates_in_file(
                binding.imported_name,
                binding.resolved_file_id,
                symbol_by_name,
                node_to_file_node,
            )
        )
    bound = tuple(sorted(set(bound_candidates)))
    if len(bound) == 1:
        return _TargetResolution(bound[0], "resolved", "explicit_import", bound)
    if len(bound) > 1:
        return _TargetResolution(None, "ambiguous", "explicit_import", bound)

    status = "ambiguous" if len(candidates) > 1 else "unresolved"
    return _TargetResolution(None, status, "unbound_name", tuple(candidates))


def _candidates_in_file(
    symbol: str,
    file_id: Optional[str],
    symbol_by_name: Dict[str, List[str]],
    node_to_file_node: Dict[str, str],
) -> List[str]:
    if not file_id or symbol in {"", "*", "default"}:
        return []
    return sorted(
        {
            node_id
            for node_id in symbol_by_name.get(symbol, [])
            if node_to_file_node.get(node_id) == file_id
        }
    )


def _edge(
    source: str,
    target: str,
    relation: str,
    metadata: Optional[Dict[str, object]] = None,
    confidence: float = 1.0,
    confidence_tier: str = _EXTRACTED,
) -> tuple:
    return (
        source,
        target,
        relation,
        json_dumps(metadata),
        confidence,
        confidence_tier,
    )


def _normalize_include_roots(
    repo_root: Path,
    include_roots: Optional[Sequence[str | Path]],
) -> tuple[str, ...]:
    if not include_roots:
        return ()

    prefixes: List[str] = []
    for raw_root in include_roots:
        raw_path = Path(raw_root)
        if raw_path.is_absolute():
            resolved = raw_path.resolve()
            try:
                rel_path = resolved.relative_to(repo_root).as_posix()
            except ValueError as exc:
                raise ValueError(
                    f"Include root '{raw_root}' is outside repository root '{repo_root}'."
                ) from exc
        else:
            rel_path = raw_path.as_posix()
        rel_path = rel_path.replace("\\", "/").strip("/")
        if rel_path in ("", "."):
            continue
        if ".." in Path(rel_path).parts:
            raise ValueError(f"Include root '{raw_root}' must stay inside the repository.")
        if rel_path not in prefixes:
            prefixes.append(rel_path)
    return tuple(prefixes)


def _include_roots_from_metadata(metadata: Dict[str, str]) -> tuple[str, ...]:
    raw_value = metadata.get("include_roots")
    if not raw_value:
        return ()
    try:
        values = json.loads(raw_value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(values, list):
        return ()
    return tuple(str(value).strip("/") for value in values if str(value).strip("/"))


def _filter_included_files(
    file_iter: Iterable[tuple],
    repo_root: Path,
    include_roots: Sequence[str],
) -> Iterable[tuple]:
    if not include_roots:
        yield from file_iter
        return
    for parser, path in file_iter:
        rel_path = path.resolve().relative_to(repo_root).as_posix()
        if _is_included_rel_path(rel_path, include_roots):
            yield parser, path


def _is_included_rel_path(rel_path: str, include_roots: Sequence[str]) -> bool:
    if not include_roots:
        return True
    normalized = rel_path.replace("\\", "/").strip("/")
    return any(normalized == root or normalized.startswith(f"{root}/") for root in include_roots)


def _indexed_file_count(index: ProjectIndex) -> int:
    row = index.conn.execute("SELECT COUNT(*) AS count FROM nodes WHERE type = 'file'").fetchone()
    return int(row["count"] if row is not None else 0)


def _missing_optional_language_warnings(
    repo_root: Path,
    *,
    exclude_patterns: Optional[Sequence[str]],
    include_roots: Sequence[str],
) -> List[str]:
    ignore = load_ignore_filter(repo_root, exclude_patterns=exclude_patterns)
    rel_paths = [
        rel_path
        for rel_path in iter_discoverable_rel_paths(repo_root, ignore=ignore)
        if _is_included_rel_path(rel_path, include_roots)
    ]
    return _missing_optional_language_warnings_for_rel_paths(rel_paths)


def _missing_optional_language_warnings_for_rel_paths(rel_paths: Iterable[str]) -> List[str]:
    ext_to_missing_language = _missing_optional_language_by_extension()
    counts: Dict[str, int] = defaultdict(int)
    for rel_path in rel_paths:
        language = ext_to_missing_language.get(Path(rel_path).suffix)
        if language:
            counts[language] += 1
    return [
        _missing_optional_language_warning(language, count)
        for language, count in sorted(counts.items())
    ]


def _missing_optional_language_by_extension() -> Dict[str, str]:
    missing: Dict[str, str] = {}
    supported_extensions = registry.supported_extensions()
    for spec in LANGUAGE_SPECS:
        if is_language_available(spec.name):
            continue
        for extension in spec.extensions:
            if extension in supported_extensions:
                continue
            missing.setdefault(extension, spec.name)
    return missing


def _missing_optional_language_warning(language: str, count: int) -> str:
    noun = "file" if count == 1 else "files"
    extra = _extra_name_for_language(language)
    return (
        f"Skipped {count} {language} {noun} because its tree-sitter grammar is not "
        f"installed. Install `csegraph[{extra}]` or `csegraph[all]` to index them."
    )


def _extra_name_for_language(language: str) -> str:
    return {
        "csharp": "csharp",
        "typescript": "all",
        "python": "all",
    }.get(language, language)
