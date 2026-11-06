from __future__ import annotations

import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from csegraph._core.core.ids import file_node_id
from csegraph._core.core.models import IndexResult, RefreshResult
from csegraph._core.ignore import load_ignore_filter
from csegraph._core.index import writer
from csegraph._core.index.cache import ExtractionCache
from csegraph._core.index.ingestion import (
    _elapsed_ms,
    _filter_included_files,
    _include_roots_from_metadata,
    _is_included_rel_path,
    _normalize_include_roots,
    _parse_one_cached,
    _parse_with_cache,
)
from csegraph._core.index.refresh_plan import (
    _existing_indexed_paths,
    _find_dependent_files,
    _indexed_untracked_from_metadata,
    _read_refresh_impact,
    _stored_file_hashes,
)
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.languages.registry import UnsupportedLanguageError, registry
from csegraph._core.languages.types import ParsedFile
from csegraph._core.repo_state import git_untracked_paths
from csegraph._core.retrieval.impact_context import (
    IMPACT_CONTEXT_TOKEN_BUDGET,
    build_impact_context,
)
from csegraph._core.retrieval.token_budget import validate_token_budget

_STRUCTURAL_LANGUAGE = "non_code"
_EXTRACTED = "EXTRACTED"


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
        with cache.batch_writes():
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
        symbol_count = sum(len(parsed.symbols) for parsed in parsed_files)

        target_path = Path(self.db_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        build_path = target_path.with_name(f".{target_path.name}.{uuid.uuid4().hex}.building")
        index = ProjectIndex(build_path)
        replaced = False
        try:
            index.begin_disposable_build()
            start = time.perf_counter()
            index.initialize_schema()
            index.begin_bulk_lexical_write()
            index.begin_bulk_secondary_index_write()

            with index.atomic_write():
                index.set_metadata(
                    repo_root,
                    include_roots=include_prefixes,
                    indexed_untracked_paths=indexed_untracked,
                    file_count=len(parsed_files),
                    symbol_count=symbol_count,
                )
                timings_ms["initialize_schema"] = _elapsed_ms(start)

                start = time.perf_counter()
                stats = writer._write_parsed_files(index, repo_root, parsed_files)
                index.finish_bulk_lexical_write()
                index.finish_bulk_secondary_index_write()
                timings_ms["write_graph"] = _elapsed_ms(start)

                start = time.perf_counter()
                parse_errors = {
                    parsed.rel_path: parsed.parse_error or ""
                    for parsed in parsed_files
                    if parsed.parse_status != "ok"
                }
                index.bump_index_revision()
                timings_ms["parse_errors"] = _elapsed_ms(start)
            index.finish_disposable_build()
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
        dependent_depth: int = 2,
        include_context: bool = True,
        task: Optional[str] = None,
        context_token_budget: int = IMPACT_CONTEXT_TOKEN_BUDGET,
        exclude_patterns: Optional[Sequence[str]] = None,
        include_roots: Optional[Sequence[str | Path]] = None,
        lease_owner: Optional[str] = None,
    ) -> RefreshResult:
        if include_context:
            validate_token_budget(context_token_budget)
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
            untracked: Optional[set[str]] = None
            candidate_indexed_untracked: Optional[List[str]] = None
            start = time.perf_counter()
            if changed_paths is not None:
                ignore = load_ignore_filter(repo_root, exclude_patterns=exclude_patterns)
                candidates: Dict[Path, str] = {}
                for p in changed_paths:
                    try:
                        resolved_p = Path(p).resolve()
                        if resolved_p.is_relative_to(repo_root):
                            candidates[resolved_p] = resolved_p.relative_to(repo_root).as_posix()
                    except Exception:
                        pass

                stored = _stored_file_hashes(index, candidates.values())

                current_files = {}
                deleted = []
                for path, rel in candidates.items():
                    if path.exists() and path.is_file():
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
                    elif rel in stored and _is_included_rel_path(rel, include_prefixes):
                        deleted.append(rel)

                previous_indexed_untracked = _indexed_untracked_from_metadata(metadata)
                new_candidates = set(current_files) - set(stored)
                new_untracked = (
                    git_untracked_paths(str(repo_root), new_candidates) if new_candidates else set()
                )
                if previous_indexed_untracked is not None or new_untracked is not None:
                    updated_untracked = set(previous_indexed_untracked or ())
                    updated_untracked.difference_update(deleted)
                    if new_untracked is not None:
                        updated_untracked.update(new_untracked & set(current_files))
                    candidate_indexed_untracked = sorted(updated_untracked)
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
                untracked = git_untracked_paths(str(repo_root))
            timings_ms["detect_changes"] = _elapsed_ms(start)

            start = time.perf_counter()
            changed: List[str] = []
            parsed_changed: List[ParsedFile] = []
            with cache.batch_writes():
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
                    indexed_untracked_paths = (
                        candidate_indexed_untracked
                        if changed_paths is not None
                        else (
                            _existing_indexed_paths(index, untracked)
                            if untracked is not None
                            else None
                        )
                    )
                    file_count = int(
                        index.conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()["c"]
                    )
                    symbol_count = int(
                        index.conn.execute("SELECT COUNT(*) AS c FROM symbols").fetchone()["c"]
                    )
                    index.set_metadata(
                        str(repo_root),
                        include_roots=include_prefixes,
                        indexed_untracked_paths=indexed_untracked_paths,
                        file_count=file_count,
                        symbol_count=symbol_count,
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
            old_node_ids = [
                *(file_node_id(rel_path) for rel_path in [*changed, *deleted]),
                *old_symbol_ids,
            ]

            changed_symbols = [
                symbol.node_id for parsed in parsed_changed for symbol in parsed.symbols
            ]
            changed_node_ids = [
                *(file_node_id(parsed.rel_path) for parsed in parsed_changed),
                *changed_symbols,
            ]

            dependents_expanded = 0
            dep_files, dependents_cap_hit = _cascade_dependent_files(
                index,
                [*old_node_ids, *changed_node_ids],
                set(changed) | set(deleted),
                dependents_limit,
                max(1, int(dependent_depth)),
            )

            dep_parsed: List[ParsedFile] = []
            if dep_files:
                with cache.batch_writes():
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
            impacted_files = sorted({*changed, *deleted, *dep_files})

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
                cleanup_symbol_ids = list(changed_symbols_deleted)
                for parsed in dep_parsed:
                    cleanup_symbol_ids.extend(
                        index.delete_file_payload(parsed.rel_path, remove_incoming=False)
                    )
                dependents_expanded = len(dep_parsed)
                timings_ms["delete_old"] = _elapsed_ms(start)

                start = time.perf_counter()
                stats = writer._write_parsed_files(
                    index,
                    str(repo_root),
                    [*parsed_changed, *dep_parsed],
                )

                index.cleanup_removed_symbol_edges(cleanup_symbol_ids)
                timings_ms["write_graph"] = _elapsed_ms(start)

                parse_errors = {
                    parsed.rel_path: parsed.parse_error or ""
                    for parsed in [*parsed_changed, *dep_parsed]
                    if parsed.parse_status != "ok"
                }
                indexed_untracked_paths = (
                    candidate_indexed_untracked
                    if changed_paths is not None
                    else (
                        _existing_indexed_paths(index, untracked) if untracked is not None else None
                    )
                )
                file_count = int(
                    index.conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()["c"]
                )
                symbol_count = int(
                    index.conn.execute("SELECT COUNT(*) AS c FROM symbols").fetchone()["c"]
                )
                index.set_metadata(
                    str(repo_root),
                    include_roots=include_prefixes,
                    indexed_untracked_paths=indexed_untracked_paths,
                    file_count=file_count,
                    symbol_count=symbol_count,
                )
                index.set_refresh_summary(changed, deleted, dep_files)
                if lease_owner and index.lease_owner(str(repo_root)) != lease_owner:
                    raise RuntimeError("Lease ownership was lost before commit.")
                index.bump_index_revision()
            seed_roles: Dict[str, str] = {}
            for symbol_id in [
                *changed_symbols,
                *(str(row["symbol_id"]) for row in _read_refresh_impact(index, changed)),
            ]:
                seed_roles[symbol_id] = "changed"
            for symbol_id in (
                str(row["symbol_id"]) for row in _read_refresh_impact(index, dep_files)
            ):
                seed_roles.setdefault(symbol_id, "impacted")
            impacted_context = (
                build_impact_context(
                    index,
                    str(repo_root),
                    seed_roles,
                    task=task,
                    token_budget=context_token_budget,
                    seed_roles=seed_roles,
                )
                if include_context
                else None
            )
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
                impacted_files=impacted_files,
                context=impacted_context,
                timings_ms=timings_ms,
            )
        finally:
            index.close()
            cache.close()


def _cascade_dependent_files(
    index: ProjectIndex,
    seed_node_ids: Sequence[str],
    already_processed: set[str],
    limit: int,
    depth: int,
) -> tuple[List[str], bool]:
    """Walk dependent files to ``depth`` hops so cascades are not truncated."""

    if not seed_node_ids or limit <= 0:
        return [], False
    collected: List[str] = []
    visited = set(already_processed)
    seen_nodes: set[str] = set()
    frontier = [node_id for node_id in dict.fromkeys(seed_node_ids) if node_id]
    cap_hit = False
    for _ in range(max(1, depth)):
        frontier = [node_id for node_id in frontier if node_id not in seen_nodes]
        if not frontier:
            break
        seen_nodes.update(frontier)
        remaining = limit - len(collected)
        if remaining <= 0:
            additional, _ = _find_dependent_files(index, frontier, visited, 1)
            cap_hit = bool(additional)
            break
        files, cap_hit = _find_dependent_files(index, frontier, visited, remaining)
        new_files = [path for path in files if path not in visited]
        if not new_files:
            break
        collected.extend(new_files)
        visited.update(new_files)
        if cap_hit:
            break
        frontier = [
            *(file_node_id(path) for path in new_files),
            *(str(row["symbol_id"]) for row in _read_refresh_impact(index, new_files)),
        ]
    return collected[:limit], cap_hit


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
