from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from csegraph.config.profiles import get_profile
from csegraph.core.ids import file_node_id
from csegraph.core.models import IndexResult, RefreshResult
from csegraph.index.repository import ProjectIndex, json_dumps
from csegraph.languages.python.parser import (
    ParsedFile,
    ParsedSymbol,
    code_tokenize,
    iter_python_files,
    module_name_from_relpath,
    parse_python_file,
    resolve_local_import,
)


class IndexService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def index(self, repo: str | Path, profile: str = "small") -> IndexResult:
        config = get_profile(profile)
        repo_root = str(Path(repo).resolve())
        parsed_files = [parse_python_file(path, Path(repo_root)) for path in iter_python_files(Path(repo_root))]

        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            project_id = index.upsert_project(repo_root, config.name)
            index.clear_project_graph(project_id)
            stats = _write_parsed_files(index, project_id, parsed_files)
            parse_errors = {
                parsed.rel_path: parsed.parse_error or ""
                for parsed in parsed_files
                if parsed.parse_status != "ok"
            }
            return IndexResult(
                command="index",
                db_path=self.db_path,
                repo_root=repo_root,
                profile=config.name,
                files_indexed=len(parsed_files),
                symbols_indexed=stats["symbols"],
                edges_indexed=stats["edges"],
                changed_files=[parsed.rel_path for parsed in parsed_files],
                parse_errors=parse_errors,
            )
        finally:
            index.close()


class RefreshService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def refresh(self, profile: str = "small") -> RefreshResult:
        config = get_profile(profile)
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            project = index.get_project()
            project_id = int(project["id"])
            repo_root = Path(project["root_dir"]).resolve()
            index.upsert_project(str(repo_root), config.name)

            current_files = {
                path.resolve().relative_to(repo_root).as_posix(): path
                for path in iter_python_files(repo_root)
            }
            stored = {
                row["path"]: row["sha256"]
                for row in index.conn.execute(
                    "SELECT path, sha256 FROM files WHERE project_id = ?",
                    (project_id,),
                )
            }

            deleted = sorted(path for path in stored if path not in current_files)
            changed: List[str] = []
            parsed_changed: List[ParsedFile] = []
            for rel_path, path in sorted(current_files.items()):
                parsed = parse_python_file(path, repo_root)
                if stored.get(rel_path) != parsed.sha256:
                    changed.append(rel_path)
                    parsed_changed.append(parsed)

            if not changed and not deleted:
                return RefreshResult(
                    command="refresh",
                    db_path=self.db_path,
                    repo_root=str(repo_root),
                    profile=config.name,
                    files_indexed=0,
                    symbols_indexed=0,
                    edges_indexed=0,
                    unchanged_files=sorted(current_files),
                )

            changed_symbols: List[str] = []
            for rel_path in deleted:
                changed_symbols.extend(
                    index.delete_file_payload(project_id, rel_path, remove_incoming=True)
                )
            for rel_path in changed:
                changed_symbols.extend(
                    index.delete_file_payload(project_id, rel_path, remove_incoming=False)
                )

            stats = _write_parsed_files(index, project_id, parsed_changed)
            index.cleanup_orphan_edges(project_id)
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
                unchanged_files=sorted(set(current_files) - set(changed)),
                changed_files=changed,
                deleted_files=deleted,
                changed_symbols=sorted(set(changed_symbols)),
                parse_errors=parse_errors,
            )
        finally:
            index.close()


def _write_parsed_files(
    index: ProjectIndex,
    project_id: int,
    parsed_files: Sequence[ParsedFile],
) -> Dict[str, int]:
    now = time.time()
    file_id_by_path: Dict[str, int] = {}
    for parsed in parsed_files:
        index.conn.execute(
            """
            INSERT INTO files(
                project_id, path, language, sha256, mtime, size,
                parse_status, parse_error, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, path) DO UPDATE SET
                language = excluded.language,
                sha256 = excluded.sha256,
                mtime = excluded.mtime,
                size = excluded.size,
                parse_status = excluded.parse_status,
                parse_error = excluded.parse_error,
                updated_at = excluded.updated_at
            """,
            (
                project_id,
                parsed.rel_path,
                parsed.language,
                parsed.sha256,
                parsed.mtime,
                parsed.size,
                parsed.parse_status,
                parsed.parse_error,
                now,
            ),
        )
        row = index.conn.execute(
            "SELECT id FROM files WHERE project_id = ? AND path = ?",
            (project_id, parsed.rel_path),
        ).fetchone()
        file_id_by_path[parsed.rel_path] = int(row["id"])

        file_summary = _file_summary(parsed)
        _upsert_summary(index, project_id, file_node_id(parsed.rel_path), parsed.sha256, "file", file_summary)
        _replace_lexical(index, file_node_id(parsed.rel_path), f"{parsed.rel_path} {file_summary}")

    symbol_by_name: Dict[str, List[str]] = defaultdict(list)
    node_to_file_node: Dict[str, str] = {}
    for row in index.conn.execute(
        """
        SELECT s.id, s.name, f.path
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.project_id = ?
        """,
        (project_id,),
    ):
        symbol_by_name[row["name"]].append(row["id"])
        if "." in row["name"]:
            symbol_by_name[row["name"].split(".")[-1]].append(row["id"])
        node_to_file_node[row["id"]] = file_node_id(row["path"])

    for parsed in parsed_files:
        file_id = file_id_by_path[parsed.rel_path]
        for symbol in parsed.symbols:
            index.conn.execute(
                """
                INSERT OR REPLACE INTO symbols(
                    id, project_id, file_id, kind, name, parent_symbol_id,
                    signature, docstring, start_line, end_line, source_hash
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol.node_id,
                    project_id,
                    file_id,
                    symbol.kind,
                    symbol.name,
                    symbol.parent_symbol_id,
                    symbol.signature,
                    symbol.docstring,
                    symbol.start_line,
                    symbol.end_line,
                    symbol.source_hash,
                ),
            )
            symbol_by_name[symbol.name].append(symbol.node_id)
            if "." in symbol.name:
                symbol_by_name[symbol.name.split(".")[-1]].append(symbol.node_id)
            node_to_file_node[symbol.node_id] = file_node_id(parsed.rel_path)

            summary = _symbol_summary(symbol)
            _upsert_summary(index, project_id, symbol.node_id, symbol.source_hash, symbol.kind, summary)
            _replace_lexical(
                index,
                symbol.node_id,
                " ".join(
                    [
                        symbol.name,
                        parsed.rel_path,
                        symbol.signature,
                        symbol.docstring,
                        summary,
                        " ".join(code_tokenize(symbol.source)),
                    ]
                ),
            )
    index.conn.commit()

    module_to_file_id = _module_to_file_id(index, project_id)
    edges_inserted = 0
    for parsed in parsed_files:
        current_module = module_name_from_relpath(parsed.rel_path)
        current_file_id = file_node_id(parsed.rel_path)
        for symbol in parsed.symbols:
            source = symbol.parent_symbol_id if symbol.kind == "method" and symbol.parent_symbol_id else current_file_id
            if _insert_edge(index, project_id, source, symbol.node_id, "contains", None):
                edges_inserted += 1

        for import_name in parsed.imports:
            target_file_id = resolve_local_import(import_name, module_to_file_id, current_module)
            if target_file_id and _insert_edge(
                index,
                project_id,
                current_file_id,
                target_file_id,
                "imports",
                {"import": import_name},
            ):
                edges_inserted += 1

        for symbol in parsed.symbols:
            for call in symbol.calls:
                target = _pick_call_target(call, current_file_id, symbol_by_name, node_to_file_node)
                if target and target != symbol.node_id and _insert_edge(
                    index,
                    project_id,
                    symbol.node_id,
                    target,
                    "calls",
                    {"symbol": call},
                ):
                    edges_inserted += 1

    index.conn.commit()
    total_edges = int(
        index.conn.execute(
            "SELECT COUNT(*) AS count FROM edges WHERE project_id = ?",
            (project_id,),
        ).fetchone()["count"]
    )
    return {
        "symbols": sum(len(parsed.symbols) for parsed in parsed_files),
        "edges": total_edges if len(parsed_files) > 1 else edges_inserted,
    }


def _insert_edge(
    index: ProjectIndex,
    project_id: int,
    source_id: str,
    target_id: str,
    relation: str,
    metadata: Optional[Dict[str, Any]],
) -> bool:
    before = index.conn.total_changes
    index.conn.execute(
        """
        INSERT OR IGNORE INTO edges(project_id, source_id, target_id, relation, metadata)
        VALUES(?, ?, ?, ?, ?)
        """,
        (project_id, source_id, target_id, relation, json_dumps(metadata)),
    )
    return index.conn.total_changes > before


def _upsert_summary(
    index: ProjectIndex,
    project_id: int,
    node_id: str,
    source_hash: str,
    kind: str,
    summary: str,
) -> None:
    index.conn.execute(
        """
        INSERT INTO summaries(node_id, project_id, source_hash, summary, kind, updated_at)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(node_id) DO UPDATE SET
            project_id = excluded.project_id,
            source_hash = excluded.source_hash,
            summary = excluded.summary,
            kind = excluded.kind,
            updated_at = excluded.updated_at
        """,
        (node_id, project_id, source_hash, summary, kind, time.time()),
    )


def _replace_lexical(index: ProjectIndex, node_id: str, content: str) -> None:
    index.conn.execute("DELETE FROM lexical_index WHERE node_id = ?", (node_id,))
    index.conn.execute(
        "INSERT INTO lexical_index(node_id, content) VALUES(?, ?)",
        (node_id, content),
    )


def _file_summary(parsed: ParsedFile) -> str:
    names = ", ".join(symbol.name for symbol in parsed.symbols[:8]) or "no symbols"
    return f"Module {parsed.rel_path} defines {names}."


def _symbol_summary(symbol: ParsedSymbol) -> str:
    parts = [symbol.signature or f"{symbol.kind} {symbol.name}"]
    if symbol.docstring:
        parts.append(symbol.docstring.split(".")[0].replace("\n", " ").strip())
    if symbol.calls:
        parts.append("calls " + ", ".join(symbol.calls[:8]))
    return " - ".join(part for part in parts if part)


def _module_to_file_id(index: ProjectIndex, project_id: int) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for row in index.conn.execute(
        "SELECT path FROM files WHERE project_id = ?",
        (project_id,),
    ):
        mapping[module_name_from_relpath(row["path"])] = file_node_id(row["path"])
    return mapping


def _pick_call_target(
    symbol: str,
    current_file_id: str,
    symbol_by_name: Dict[str, List[str]],
    node_to_file_node: Dict[str, str],
) -> Optional[str]:
    candidates = symbol_by_name.get(symbol, [])
    if not candidates:
        return None
    for node_id in candidates:
        if node_to_file_node.get(node_id) == current_file_id:
            return node_id
    return candidates[0]
