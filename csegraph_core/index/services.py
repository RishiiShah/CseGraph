from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from csegraph_core.config.profiles import get_profile
from csegraph_core.core.ids import file_node_id, folder_node_id, repo_node_id
from csegraph_core.core.models import IndexResult, RefreshResult
from csegraph_core.ignore import load_ignore_filter
from csegraph_core.index.cache import ExtractionCache
from csegraph_core.index.repository import ProjectIndex, json_dumps
from csegraph_core.languages.registry import UnsupportedLanguageError, registry
from csegraph_core.languages.types import ParsedFile, ParsedSymbol

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

    def index(self, repo: str | Path, profile: str = "small") -> IndexResult:
        timings_ms: Dict[str, float] = {}
        config = get_profile(profile)
        repo_root = str(Path(repo).resolve())
        cache_path = str(Path(self.db_path).with_name("parse_cache.db"))
        cache = ExtractionCache(cache_path)
        start = time.perf_counter()
        parsed_files = _parse_with_cache(
            registry.iter_files(Path(repo_root)), Path(repo_root), cache,
        )
        timings_ms["discover_parse"] = _elapsed_ms(start)

        index = ProjectIndex(self.db_path)
        try:
            start = time.perf_counter()
            index.initialize_schema()
            index.set_metadata(repo_root, config.name)
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
                timings_ms=timings_ms,
            )
        finally:
            index.close()
            cache.close()


class RefreshService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def refresh(
        self,
        profile: str = "small",
        changed_paths: Optional[Iterable[str | Path]] = None,
    ) -> RefreshResult:
        config = get_profile(profile)
        cache_path = str(Path(self.db_path).with_name("parse_cache.db"))
        cache = ExtractionCache(cache_path)
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = Path(metadata["root_dir"]).resolve()
            index.set_metadata(str(repo_root), config.name)

            if changed_paths is not None:
                ignore = load_ignore_filter(repo_root)
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
                for path in changed_abs_set:
                    if path.exists() and path.is_file():
                        rel = path.relative_to(repo_root).as_posix()
                        if ignore.is_ignored(rel):
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
                            if rel in stored:
                                deleted.append(rel)
                        except Exception:
                            pass
            else:
                current_files = {
                    path.resolve().relative_to(repo_root).as_posix(): (parser, path)
                    for parser, path in registry.iter_files(repo_root)
                }
                stored = {
                    row["path"]: row["sha256"]
                    for row in index.conn.execute(
                        "SELECT path, sha256 FROM nodes WHERE type = 'file'",
                    )
                }
                deleted = sorted(path for path in stored if path not in current_files)

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
                )

            changed_symbols: List[str] = []
            for rel_path in deleted:
                changed_symbols.extend(index.delete_file_payload(rel_path, remove_incoming=True))
            for rel_path in changed:
                changed_symbols.extend(index.delete_file_payload(rel_path, remove_incoming=False))

            stats = _write_parsed_files(index, str(repo_root), parsed_changed)
            index.cleanup_orphan_edges()
            index.cleanup_orphan_folders()
            changed_symbols.extend(symbol.node_id for parsed in parsed_changed for symbol in parsed.symbols)
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
            )
        finally:
            index.close()
            cache.close()


def _resolve_cross_file_methods(index: ProjectIndex) -> None:
    # 1. Update nodes table to link methods to classes across files
    index.conn.execute(
        """
        UPDATE nodes
        SET parent_id = COALESCE(
            (
                SELECT c.id FROM nodes c
                WHERE c.type = 'class' AND c.name = SUBSTR(nodes.name, 1, INSTR(nodes.name, '.') - 1)
                ORDER BY (SUBSTR(c.path, 1, INSTR(c.path, '/') - 1) = SUBSTR(nodes.path, 1, INSTR(nodes.path, '/') - 1)) DESC, c.id ASC
                LIMIT 1
            ),
            parent_id
        )
        WHERE nodes.type = 'method'
          AND nodes.parent_id LIKE 'file::%'
          AND INSTR(nodes.name, '.') > 1
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
    for parsed in parsed_files:
        node_id = file_node_id(parsed.rel_path)
        parent_dir = "/".join(parsed.rel_path.split("/")[:-1])
        parent = folder_node_id(parent_dir) if parent_dir else repo_node_id(Path(repo_root).name or "repo")
        file_meta = json.dumps({"size": parsed.size, "mtime": parsed.mtime}, sort_keys=True)
        file_node_rows.append(
            (
                node_id, parent, Path(parsed.rel_path).name, parsed.rel_path,
                parsed.language, parsed.sha256, parsed.sha256,
                parsed.parse_status, parsed.parse_error, file_meta, now,
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


def _load_symbol_lookup(index: ProjectIndex, batch: _WriteBatch) -> None:
    for row in index.conn.execute(
        """
        SELECT id, name, path, type FROM nodes
        WHERE type IN ('class','function','method')
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
                    symbol.node_id, parent, symbol.kind, symbol.name, parsed.rel_path,
                    parsed.language,
                    symbol.signature, symbol.docstring, symbol.start_line, symbol.end_line,
                    symbol.source_hash, metadata, 1 if symbol.is_test else 0, now,
                )
            )
            batch.symbol_by_name[symbol.name].append(symbol.node_id)
            if "." in symbol.name:
                batch.symbol_by_name[symbol.name.split(".")[-1]].append(symbol.node_id)
            batch.node_to_file_node[symbol.node_id] = file_node_id(parsed.rel_path)
            batch.node_kind_by_id[symbol.node_id] = symbol.kind

            summary = _symbol_summary(symbol)
            batch.summary_rows.append((symbol.node_id, symbol.source_hash, summary, symbol.kind, now))
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
    for parsed in parsed_files:
        try:
            parser = registry.for_extension(Path(parsed.rel_path).suffix)
        except UnsupportedLanguageError:
            continue
        current_module = parser.module_name_from_relpath(parsed.rel_path)
        current_file_id = file_node_id(parsed.rel_path)
        for symbol in parsed.symbols:
            source = symbol.parent_symbol_id if symbol.kind == "method" and symbol.parent_symbol_id else current_file_id
            edge_rows.append(_edge(source, symbol.node_id, "contains"))

        for import_name in parsed.imports:
            target_file_id = parser.resolve_local_import(import_name, module_to_file_id, current_module)
            if target_file_id:
                edge_rows.append(_edge(current_file_id, target_file_id, "imports", {"import": import_name}))

        _SYMBOL_EDGE_SPECS = [
            ("calls", "calls", "symbol", False),
            ("bases", "inherits", "base", False),
            ("decorators", "decorates", "decorator", True),
        ]
        for symbol in parsed.symbols:
            for attr, relation, meta_key, reverse in _SYMBOL_EDGE_SPECS:
                for name in getattr(symbol, attr):
                    target = _pick_call_target(
                        name,
                        current_file_id,
                        batch.symbol_by_name,
                        batch.node_to_file_node,
                        batch.node_kind_by_id,
                    )
                    if target and target != symbol.node_id:
                        src, tgt = (target, symbol.node_id) if reverse else (symbol.node_id, target)
                        edge_rows.append(_edge(src, tgt, relation, {meta_key: name}))
            if symbol.is_test:
                for call in symbol.calls:
                    target = _pick_call_target(
                        call,
                        current_file_id,
                        batch.symbol_by_name,
                        batch.node_to_file_node,
                        batch.node_kind_by_id,
                    )
                    if target and target != symbol.node_id:
                        edge_rows.append(_edge(target, symbol.node_id, "tested_by", {"via": call}))

    if edge_rows:
        index.conn.executemany(
            """
            INSERT OR IGNORE INTO edges(source, target, relation, metadata, confidence, confidence_tier)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            edge_rows,
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
    from csegraph_core.languages.base import sha256_text
    resolved_path = path.resolve()
    resolved_root = Path(repo_root).resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"Path '{path}' resolves to '{resolved_path}', which is outside repository root '{resolved_root}'")
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
        mapping[parser.module_name_from_relpath(path)] = file_node_id(path)
    return mapping


def _pick_call_target(
    symbol: str,
    current_file_id: str,
    symbol_by_name: Dict[str, List[str]],
    node_to_file_node: Dict[str, str],
    node_kind_by_id: Dict[str, str],
) -> Optional[str]:
    candidates = symbol_by_name.get(symbol, [])
    if not candidates:
        return None
    for node_id in candidates:
        if node_to_file_node.get(node_id) == current_file_id:
            return node_id
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
