from __future__ import annotations

import json
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
from csegraph._core.index.repository import ProjectIndex, json_dumps
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
            changed_symbols: List[str] = []
            for rel_path in deleted:
                changed_symbols.extend(index.delete_file_payload(rel_path, remove_incoming=True))
            for rel_path in changed:
                changed_symbols.extend(index.delete_file_payload(rel_path, remove_incoming=False))
            timings_ms["delete_old"] = _elapsed_ms(start)

            start = time.perf_counter()
            stats = _write_parsed_files(index, str(repo_root), parsed_changed)
            index.cleanup_orphan_edges()
            index.cleanup_orphan_folders()
            timings_ms["write_graph"] = _elapsed_ms(start)

            changed_symbols.extend(
                symbol.node_id for parsed in parsed_changed for symbol in parsed.symbols
            )

            # --- P5-4: bounded dependent expansion ---
            start = time.perf_counter()
            dependents_expanded = 0
            dependents_cap_hit = False
            if changed_symbols and dependents_limit > 0:
                dep_files, cap_hit = _find_dependent_files(
                    index,
                    changed_symbols,
                    set(changed) | set(deleted),
                    dependents_limit,
                )
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
        JOIN nodes n ON n.id = e.source
        WHERE e.target IN ({placeholders})
          AND e.relation IN ('calls', 'imports', 'inherits')
          AND n.type IN ('file', 'class', 'function', 'method', 'test', 'document')
          AND n.path IS NOT NULL
        LIMIT ?
        """,
        (*changed_symbol_ids, limit + 1),
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
    reference_rows: List[tuple] = []
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

        imported_file_ids: List[str] = []
        import_aliases: Dict[str, str] = {}
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
            if target_file_id:
                if target_file_id not in imported_file_ids:
                    imported_file_ids.append(target_file_id)
                import_aliases.update(_import_aliases(import_record))
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
            for attr, relation, meta_key, reverse in _SYMBOL_EDGE_SPECS:
                for name in getattr(symbol, attr):
                    target = _pick_call_target(
                        import_aliases.get(name, name),
                        current_file_id,
                        batch.symbol_by_name,
                        batch.node_to_file_node,
                        batch.node_kind_by_id,
                        imported_file_ids,
                    )
                    if target and target != symbol.node_id:
                        src, tgt = (target, symbol.node_id) if reverse else (symbol.node_id, target)
                        edge_rows.append(_edge(src, tgt, relation, {meta_key: name}))
                        for reference in _references_for(symbol, relation, name):
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
                                    reference.metadata,
                                )
                            )
            if symbol.is_test:
                for call in symbol.calls:
                    target = _pick_call_target(
                        import_aliases.get(call, call),
                        current_file_id,
                        batch.symbol_by_name,
                        batch.node_to_file_node,
                        batch.node_kind_by_id,
                        imported_file_ids,
                    )
                    if target and target != symbol.node_id:
                        references = _test_references_for(symbol, call)
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
                                },
                            )
                        )
                        for reference in references:
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
                                    reference.metadata,
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


def _import_aliases(import_record: ParsedImport) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    raw_aliases = import_record.metadata.get("aliases")
    if isinstance(raw_aliases, dict):
        for local, target in raw_aliases.items():
            if isinstance(local, str) and isinstance(target, str) and target != "*":
                aliases[local] = _symbol_name_from_import_target(target)

    raw_imports = import_record.metadata.get("imports")
    if isinstance(raw_imports, list):
        for item in raw_imports:
            if not isinstance(item, dict):
                continue
            local = item.get("local")
            target = item.get("qualified") or item.get("name")
            if isinstance(local, str) and isinstance(target, str) and target != "*":
                aliases[local] = _symbol_name_from_import_target(target)
    return aliases


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
    target: str,
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
    candidates = symbol_by_name.get(symbol, [])
    if not candidates:
        return None
    for node_id in candidates:
        if node_to_file_node.get(node_id) == current_file_id:
            return node_id
    preferred_rank = {file_id: index for index, file_id in enumerate(preferred_file_ids or [])}
    imported_candidates = [
        node_id for node_id in candidates if node_to_file_node.get(node_id) in preferred_rank
    ]
    if imported_candidates:
        return min(
            imported_candidates,
            key=lambda node_id: (
                preferred_rank[node_to_file_node[node_id]],
                node_kind_by_id.get(node_id) != "function",
                node_id,
            ),
        )
    for node_id in candidates:
        if node_kind_by_id.get(node_id) == "function":
            return node_id
    return candidates[0]


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
