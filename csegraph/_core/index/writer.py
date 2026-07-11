"""SQLite persistence for parsed repository files and graph relationships."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence

from csegraph._core.core.ids import file_node_id
from csegraph._core.index.ingestion import _file_summary, _symbol_summary
from csegraph._core.index.lookups import _LazySymbolLookup
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.index.resolution import (
    _edge,
    _edge_occurrence,
    _import_bindings_for,
    _ImportBinding,
    _module_to_file_id,
    _normalized_import_records,
    _references_for,
    _resolve_call_target,
    _TargetResolution,
    _test_references_for,
    _with_occurrence_keys,
)
from csegraph._core.languages.registry import UnsupportedLanguageError, registry
from csegraph._core.languages.types import ParsedFile


@dataclass
class _WriteBatch:
    summary_rows: List[tuple] = field(default_factory=list)
    lexical_rows: List[tuple] = field(default_factory=list)
    symbol_by_name: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))
    node_to_file_node: Dict[str, str] = field(default_factory=dict)
    node_kind_by_id: Dict[str, str] = field(default_factory=dict)


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
    del repo_root
    _validate_parsed_languages(parsed_files)
    now = time.time()
    batch = _WriteBatch()

    _insert_file_nodes(index, parsed_files, now, batch)
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
    parsed_files: Sequence[ParsedFile],
    now: float,
    batch: _WriteBatch,
) -> None:
    file_rows: List[tuple] = []
    module_rows: List[tuple] = []
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
        batch.lexical_rows.append(
            (node_id, Path(parsed.rel_path).name, parsed.rel_path, "", "", file_summary, "")
        )
        try:
            parser = registry.for_extension(Path(parsed.rel_path).suffix)
        except UnsupportedLanguageError:
            continue
        module_name = parser.module_name_from_relpath(parsed.rel_path)
        if module_name is not None:
            module_rows.append((module_name, node_id))

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
    if module_rows:
        index.conn.executemany(
            """
            INSERT INTO module_lookup(module_name, file_id)
            VALUES(?, ?)
            ON CONFLICT(module_name, file_id) DO NOTHING
            """,
            module_rows,
        )


def _load_symbol_lookup(index: ProjectIndex, batch: _WriteBatch) -> None:
    existing = index.conn.execute("SELECT 1 FROM symbols LIMIT 1").fetchone()
    if existing is None:
        return
    batch.symbol_by_name = _LazySymbolLookup(
        index,
        batch.node_to_file_node,
        batch.node_kind_by_id,
    )


def _insert_symbol_nodes(
    index: ProjectIndex,
    parsed_files: Sequence[ParsedFile],
    now: float,
    batch: _WriteBatch,
) -> None:
    symbol_rows: List[tuple] = []
    lookup_rows: List[tuple] = []
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
            lookup_names = {symbol.name}
            if "." in symbol.name:
                lookup_names.add(symbol.name.rsplit(".", 1)[-1])
            for lookup_name in lookup_names:
                if isinstance(batch.symbol_by_name, _LazySymbolLookup):
                    batch.symbol_by_name.add_candidate(lookup_name, symbol.node_id)
                else:
                    batch.symbol_by_name[lookup_name].append(symbol.node_id)
            batch.node_to_file_node[symbol.node_id] = file_node_id(parsed.rel_path)
            batch.node_kind_by_id[symbol.node_id] = symbol.kind
            lookup_rows.extend(
                (lookup_name, symbol.node_id) for lookup_name in sorted(lookup_names)
            )

            summary = _symbol_summary(symbol)
            batch.summary_rows.append(
                (symbol.node_id, symbol.source_hash, summary, symbol.kind, now)
            )
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
    if lookup_rows:
        index.conn.executemany(
            """
            INSERT INTO symbol_lookup(lookup_name, symbol_id)
            VALUES(?, ?)
            ON CONFLICT(lookup_name, symbol_id) DO NOTHING
            """,
            lookup_rows,
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

    if batch.lexical_rows:
        index.conn.executemany(
            """
            INSERT INTO lexical_documents(
                node_id, name, path, signature, docstring, summary, source
            )
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                name = excluded.name,
                path = excluded.path,
                signature = excluded.signature,
                docstring = excluded.docstring,
                summary = excluded.summary,
                source = excluded.source
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
                batch.symbol_by_name,
                batch.node_to_file_node,
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


__all__ = ["_WriteBatch", "_write_parsed_files"]
