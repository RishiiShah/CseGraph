from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from hashlib import blake2b
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from csegraph._core.core.ids import file_node_id
from csegraph._core.core.models import IndexResult, RefreshResult
from csegraph._core.ignore import load_ignore_filter
from csegraph._core.index.cache import ExtractionCache
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.languages.registry import UnsupportedLanguageError, registry
from csegraph._core.languages.types import (
    ParsedFile,
    ParsedImport,
    ParsedReference,
    ParsedSymbol,
)
from csegraph._core.repo_state import git_untracked_paths

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


@dataclass(frozen=True)
class _TargetResolution:
    target: Optional[str]
    status: str
    strategy: str
    candidates: tuple[str, ...] = ()


class IndexService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def index(
        self,
        repo: str | Path,
        *,
        exclude_patterns: Optional[Sequence[str]] = None,
        include_roots: Optional[Sequence[str | Path]] = None,
        lease_owner: Optional[str] = None,
    ) -> IndexResult:
        timings_ms: Dict[str, float] = {}
        repo_root_path = Path(repo).resolve()
        repo_root = str(repo_root_path)
        include_prefixes = _normalize_include_roots(repo_root_path, include_roots)
        cache_path = str(Path(self.db_path).with_name("parse_cache.db"))
        cache = ExtractionCache(cache_path)
        start = time.perf_counter()
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
        untracked = git_untracked_paths(repo_root)
        indexed_untracked = (
            sorted({parsed.rel_path for parsed in parsed_files} & untracked)
            if untracked is not None
            else None
        )

        target_path = Path(self.db_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        build_path = target_path.with_name(f".{target_path.name}.{uuid.uuid4().hex}.building")
        index = ProjectIndex(build_path)
        replaced = False
        try:
            start = time.perf_counter()
            index.initialize_schema()

            with index.atomic_write():
                index.set_metadata(
                    repo_root,
                    include_roots=include_prefixes,
                    indexed_untracked_paths=indexed_untracked,
                )
                timings_ms["initialize_schema"] = _elapsed_ms(start)

                start = time.perf_counter()
                stats = _write_parsed_files(index, repo_root, parsed_files)
                timings_ms["write_graph"] = _elapsed_ms(start)

                start = time.perf_counter()
                parse_errors = {
                    parsed.rel_path: parsed.parse_error or ""
                    for parsed in parsed_files
                    if parsed.parse_status != "ok"
                }
                index.bump_index_revision()
                timings_ms["parse_errors"] = _elapsed_ms(start)
            index.validate_integrity()
            index.optimize()
            index.close()
            if lease_owner and not _target_lease_owned(target_path, repo_root, lease_owner):
                raise RuntimeError("Lease ownership was lost before index replacement.")
            os.replace(build_path, target_path)
            replaced = True
            return IndexResult(
                files_indexed=len(parsed_files),
                symbols_indexed=stats["symbols"],
                edges_indexed=stats["edges"],
                cache_hits=cache.hits,
                cache_misses=cache.misses,
                changed_files=[parsed.rel_path for parsed in parsed_files],
                parse_errors=parse_errors,
                timings_ms=timings_ms,
            )
        finally:
            if not replaced:
                index.close()
            for artifact in (build_path, Path(f"{build_path}-wal"), Path(f"{build_path}-shm")):
                artifact.unlink(missing_ok=True)
            cache.close()


class RefreshService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def refresh(
        self,
        changed_paths: Optional[Iterable[str | Path]] = None,
        dependents_limit: int = 50,
        *,
        exclude_patterns: Optional[Sequence[str]] = None,
        include_roots: Optional[Sequence[str | Path]] = None,
        lease_owner: Optional[str] = None,
    ) -> RefreshResult:
        cache_path = str(Path(self.db_path).with_name("parse_cache.db"))
        cache = ExtractionCache(cache_path)
        index = ProjectIndex(self.db_path)
        timings_ms: Dict[str, float] = {}
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = Path(metadata["root_dir"]).resolve()
            include_prefixes = (
                _normalize_include_roots(repo_root, include_roots)
                if include_roots is not None
                else _include_roots_from_metadata(metadata)
            )
            untracked = git_untracked_paths(str(repo_root))
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
                    for row in index.conn.execute("SELECT path, sha256 FROM files")
                }

                current_files = {}
                deleted = []
                for path in changed_abs_set:
                    if path.exists() and path.is_file():
                        rel = path.relative_to(repo_root).as_posix()
                        # ``changed_paths`` explicitly includes untracked
                        # files. Ordinary Git discovery intentionally limits
                        # itself to tracked paths, but applying that rule here
                        # would detect an untracked source and then silently
                        # refuse to refresh it. Explicit paths still honor
                        # include roots and every ignore rule.
                        if not _is_included_rel_path(rel, include_prefixes) or ignore.is_ignored(
                            rel
                        ):
                            if rel in stored:
                                deleted.append(rel)
                            continue
                        try:
                            parser = registry.for_extension(path.suffix)
                            current_files[rel] = (parser, path)
                        except UnsupportedLanguageError:
                            pass

                for path in changed_abs_set:
                    if not path.exists():
                        try:
                            rel = path.relative_to(repo_root).as_posix()
                            if rel in stored and _is_included_rel_path(rel, include_prefixes):
                                deleted.append(rel)
                        except Exception:
                            pass
            else:
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
                        "SELECT path, sha256 FROM files",
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
                with index.atomic_write():
                    indexed_untracked = (
                        _existing_indexed_paths(index, untracked) if untracked is not None else None
                    )
                    index.set_metadata(
                        str(repo_root),
                        include_roots=include_prefixes,
                        indexed_untracked_paths=indexed_untracked,
                    )
                    if lease_owner and not index.verify_lease(str(repo_root), lease_owner):
                        raise RuntimeError("Lease ownership was lost before commit.")
                return RefreshResult(
                    files_indexed=0,
                    symbols_indexed=0,
                    edges_indexed=0,
                    cache_hits=cache.hits,
                    cache_misses=cache.misses,
                    unchanged_files=sorted(set(stored.keys()) - set(changed) - set(deleted)),
                    timings_ms=timings_ms,
                )

            start = time.perf_counter()
            impact_snapshot = _read_refresh_impact(index, [*changed, *deleted])
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

            changed_symbols = [
                symbol.node_id for parsed in parsed_changed for symbol in parsed.symbols
            ]

            # --- P5-4: bounded dependent expansion ---
            dependents_expanded = 0
            dependents_cap_hit = pre_dependents_cap_hit
            dep_files = list(pre_dependents)
            if changed_symbols and dependents_limit > 0:
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

            dep_parsed: List[ParsedFile] = []
            if dep_files:
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
            timings_ms["dependent_expansion"] = _elapsed_ms(start)

            start = time.perf_counter()
            stats = {"symbols": 0, "edges": 0}
            with index.atomic_write():
                changed_symbols_deleted: List[str] = []
                for rel_path in deleted:
                    changed_symbols_deleted.extend(
                        index.delete_file_payload(rel_path, remove_incoming=True)
                    )
                for rel_path in changed:
                    changed_symbols_deleted.extend(
                        index.delete_file_payload(rel_path, remove_incoming=False)
                    )
                timings_ms["delete_old"] = _elapsed_ms(start)

                start = time.perf_counter()
                stats = _write_parsed_files(index, str(repo_root), parsed_changed)

                if dep_parsed:
                    for parsed in dep_parsed:
                        index.delete_file_payload(parsed.rel_path, remove_incoming=False)
                    dep_stats = _write_parsed_files(index, str(repo_root), dep_parsed)
                    dependents_expanded = len(dep_parsed)
                    stats["symbols"] += dep_stats["symbols"]
                    stats["edges"] = dep_stats["edges"]

                index.cleanup_orphan_edges()
                timings_ms["write_graph"] = _elapsed_ms(start)

                parse_errors = {
                    parsed.rel_path: parsed.parse_error or ""
                    for parsed in [*parsed_changed, *dep_parsed]
                    if parsed.parse_status != "ok"
                }
                indexed_untracked = (
                    _existing_indexed_paths(index, untracked) if untracked is not None else None
                )
                index.set_metadata(
                    str(repo_root),
                    include_roots=include_prefixes,
                    indexed_untracked_paths=indexed_untracked,
                )
                if lease_owner and not index.verify_lease(str(repo_root), lease_owner):
                    raise RuntimeError("Lease ownership was lost before commit.")
                index.bump_index_revision()
            changed_symbols.extend(changed_symbols_deleted)
            return RefreshResult(
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
                dependents_expanded=dependents_expanded,
                dependents_cap_hit=dependents_cap_hit,
                timings_ms=timings_ms,
            )
        finally:
            index.close()
            cache.close()


def _existing_indexed_paths(
    index: ProjectIndex,
    candidates: Iterable[str],
) -> List[str]:
    unique = sorted(set(candidates))
    existing: List[str] = []
    for offset in range(0, len(unique), 400):
        batch = unique[offset : offset + 400]
        placeholders = ",".join("?" for _ in batch)
        existing.extend(
            str(row["path"])
            for row in index.conn.execute(
                f"SELECT path FROM files WHERE path IN ({placeholders})",
                tuple(batch),
            )
        )
    return sorted(existing)


def _read_refresh_impact(
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
                s.id AS symbol_id
            FROM symbols AS s
            JOIN files AS f ON f.id = s.file_id
            WHERE f.path IN ({placeholders})
            """,
            tuple(rel_paths),
        )
    ]
    return snapshots


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
        JOIN entities n ON n.id = CASE
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
    index.conn.execute(
        """
        WITH methods AS (
            SELECT s.id, s.name, f.path
            FROM symbols AS s
            JOIN files AS f ON f.id = s.file_id
            WHERE s.kind = 'method'
              AND parent_id LIKE 'file::%'
              AND INSTR(s.name, '.') > 1
        ),
        candidates AS (
            SELECT
                m.id AS method_id,
                c.id AS class_id,
                ROW_NUMBER() OVER (
                    PARTITION BY m.id
                    ORDER BY (SUBSTR(cf.path, 1, INSTR(cf.path, '/') - 1)
                            = SUBSTR(m.path, 1, INSTR(m.path, '/') - 1)) DESC,
                             c.id ASC
                ) AS rn
            FROM methods m
            JOIN symbols c ON c.kind = 'class'
                AND c.name = SUBSTR(m.name, 1, INSTR(m.name, '.') - 1)
            JOIN files cf ON cf.id = c.file_id
        )
        UPDATE symbols
        SET parent_id = (
            SELECT class_id FROM candidates WHERE method_id = symbols.id AND rn = 1
        )
        WHERE id IN (SELECT method_id FROM candidates WHERE rn = 1)
        """
    )


def _write_parsed_files(
    index: ProjectIndex,
    repo_root: str,
    parsed_files: Sequence[ParsedFile],
) -> Dict[str, int]:
    _validate_parsed_languages(parsed_files)
    now = time.time()
    batch = _WriteBatch()

    _insert_file_nodes(index, repo_root, parsed_files, now, batch)
    _load_symbol_lookup(index, batch)
    _insert_symbol_nodes(index, parsed_files, now, batch)
    _flush_summary_and_lexical_rows(index, batch)
    _insert_edges(index, parsed_files, (), batch)
    _resolve_cross_file_methods(index)

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
    file_rows: List[tuple] = []
    for parsed in parsed_files:
        node_id = file_node_id(parsed.rel_path)
        file_rows.append(
            (
                node_id,
                parsed.rel_path,
                Path(parsed.rel_path).name,
                parsed.language,
                parsed.sha256,
                parsed.parse_status,
                parsed.parse_error,
                parsed.size,
                parsed.mtime,
                now,
            )
        )
        file_summary = _file_summary(parsed)
        batch.summary_rows.append((node_id, parsed.sha256, file_summary, "file", now))
        batch.lexical_delete_ids.append((node_id,))
        batch.lexical_rows.append(
            (node_id, Path(parsed.rel_path).name, parsed.rel_path, "", "", file_summary, "")
        )

    if file_rows:
        index.conn.executemany(
            """
            INSERT INTO files(
                id, path, name, language, sha256,
                parse_status, parse_error, size, mtime, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                path = excluded.path,
                    name = excluded.name,
                    language = excluded.language,
                    sha256 = excluded.sha256,
                    parse_status = excluded.parse_status,
                parse_error = excluded.parse_error,
                size = excluded.size,
                mtime = excluded.mtime,
                updated_at = excluded.updated_at
            """,
            file_rows,
        )


def _load_symbol_lookup(index: ProjectIndex, batch: _WriteBatch) -> None:
    for row in index.conn.execute(
        """
        SELECT id, name, path, type FROM entities
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
    symbol_rows: List[tuple] = []
    for parsed in parsed_files:
        tokenizer = registry.tokenizer_for(parsed.language)
        for symbol in parsed.symbols:
            symbol_rows.append(
                (
                    symbol.node_id,
                    file_node_id(parsed.rel_path),
                    symbol.parent_symbol_id,
                    symbol.kind,
                    symbol.name,
                    symbol.signature,
                    symbol.docstring,
                    symbol.start_line,
                    symbol.end_line,
                    symbol.source_hash,
                    1 if symbol.is_test else 0,
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

    if symbol_rows:
        index.conn.executemany(
            """
            INSERT INTO symbols(
                id, file_id, parent_id, kind, name, signature, docstring,
                start_line, end_line, source_hash, is_test, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                file_id = excluded.file_id,
                parent_id = excluded.parent_id,
                kind = excluded.kind,
                name = excluded.name,
                signature = excluded.signature,
                docstring = excluded.docstring,
                start_line = excluded.start_line,
                end_line = excluded.end_line,
                source_hash = excluded.source_hash,
                is_test = excluded.is_test,
                updated_at = excluded.updated_at
            """,
            symbol_rows,
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
    occurrence_rows: List[tuple] = []
    for parsed in parsed_files:
        try:
            parser = registry.for_extension(Path(parsed.rel_path).suffix)
        except UnsupportedLanguageError:
            continue
        current_module = parser.module_name_from_relpath(parsed.rel_path)
        current_file_id = file_node_id(parsed.rel_path)

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
                    import_name,
                    target_file_id,
                    import_record.start_line,
                    import_record.end_line,
                    import_record.source,
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
                )
            )
            if target_file_id:
                edge_rows.append(
                    _edge(
                        current_file_id,
                        target_file_id,
                        "imports",
                    )
                )

        _SYMBOL_EDGE_SPECS = [
            ("calls", "calls", False),
            ("bases", "inherits", False),
            ("decorators", "decorates", True),
        ]
        for symbol in parsed.symbols:
            call_resolutions: Dict[str, _TargetResolution] = {}
            for attr, relation, reverse in _SYMBOL_EDGE_SPECS:
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
                        edge_rows.append(
                            _edge(
                                src,
                                tgt,
                                relation,
                            )
                        )
                    for reference in references:
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
                        edge_rows.append(
                            _edge(
                                target,
                                symbol.node_id,
                                "tested_by",
                            )
                        )
                    for reference in references:
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
                            )
                        )

    if edge_rows:
        index.conn.executemany(
            """
            INSERT INTO edges(source, target, relation, confidence, confidence_tier)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(source, target, relation) DO UPDATE SET
                confidence = excluded.confidence,
                confidence_tier = excluded.confidence_tier
            """,
            edge_rows,
        )
    if import_rows:
        index.conn.executemany(
            """
            INSERT OR REPLACE INTO imports(
                file_id, import_name, resolved_file_id, start_line, end_line, source
            )
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            import_rows,
        )
    if import_binding_rows:
        index.conn.executemany(
            """
            INSERT OR REPLACE INTO import_bindings(
                file_id, import_name, local_name, imported_name, qualified_name,
                binding_kind, resolved_file_id, resolved_symbol_id, resolution_status,
                start_line, end_line, source
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            import_binding_rows,
        )
    if occurrence_rows:
        occurrence_rows = _with_occurrence_keys(occurrence_rows)
        index.conn.executemany(
            """
            INSERT INTO edge_occurrences(
                occurrence_key, source, target, relation, source_file_id, enclosing_symbol_id,
                name, start_line, end_line, source_text, resolution_status,
                resolution_strategy, candidate_targets
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(occurrence_key) DO UPDATE SET
                source = excluded.source,
                target = excluded.target,
                resolution_status = excluded.resolution_status,
                resolution_strategy = excluded.resolution_strategy,
                candidate_targets = excluded.candidate_targets
            """,
            occurrence_rows,
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
    )


def _with_occurrence_keys(rows: Sequence[tuple]) -> List[tuple]:
    ordinals: Dict[tuple, int] = defaultdict(int)
    keyed: List[tuple] = []
    for row in rows:
        location = (row[3], row[4], row[2], row[5], row[6], row[7], row[8])
        ordinal = ordinals[location]
        ordinals[location] += 1
        raw = "\x1f".join("" if value is None else str(value) for value in (*location, ordinal))
        key = blake2b(raw.encode("utf-8"), digest_size=16).digest()
        keyed.append((key, *row))
    return keyed


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
    for row in index.conn.execute("SELECT path FROM files"):
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
    local = [node_id for node_id in candidates if node_to_file_node.get(node_id) == current_file_id]
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
        node_id for node_id in candidates if node_to_file_node.get(node_id) == current_file_id
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
    confidence: float = 1.0,
    confidence_tier: str = _EXTRACTED,
) -> tuple:
    return (
        source,
        target,
        relation,
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


def _target_lease_owned(db_path: Path, repo_root: str, owner: str) -> bool:
    if not db_path.exists():
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT owner, expires_at
                FROM refresh_leases
                WHERE repo_root = ?
                """,
                (repo_root,),
            ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None and row[0] == owner and float(row[1]) > time.time()
