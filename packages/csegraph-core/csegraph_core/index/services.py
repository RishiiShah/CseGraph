from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from csegraph_core.config.profiles import get_profile
from csegraph_core.core.ids import file_node_id, folder_node_id, repo_node_id
from csegraph_core.core.models import IndexResult, RefreshResult
from csegraph_core.index.repository import ProjectIndex, json_dumps
from csegraph_core.languages.python.parser import (
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
            stats = _write_parsed_files(index, project_id, repo_root, parsed_files)
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
                    "SELECT path, sha256 FROM nodes WHERE project_id = ? AND type = 'file'",
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

            stats = _write_parsed_files(index, project_id, str(repo_root), parsed_changed)
            index.cleanup_orphan_edges(project_id)
            index.cleanup_orphan_folders(project_id)
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
    repo_root: str,
    parsed_files: Sequence[ParsedFile],
) -> Dict[str, int]:
    now = time.time()
    _write_repo_and_folders(index, project_id, repo_root, parsed_files, now)

    file_node_rows: List[tuple] = []
    summary_rows: List[tuple] = []
    lexical_delete_ids: List[tuple] = []
    lexical_rows: List[tuple] = []
    for parsed in parsed_files:
        node_id = file_node_id(parsed.rel_path)
        parent_dir = "/".join(parsed.rel_path.split("/")[:-1])
        parent = folder_node_id(parent_dir) if parent_dir else repo_node_id(Path(repo_root).name or "repo")
        file_meta = json.dumps({"size": parsed.size, "mtime": parsed.mtime}, sort_keys=True)
        file_node_rows.append(
            (
                node_id, project_id, parent, Path(parsed.rel_path).name, parsed.rel_path,
                parsed.language, parsed.sha256, parsed.sha256,
                parsed.parse_status, parsed.parse_error, file_meta, now,
            )
        )
        file_summary = _file_summary(parsed)
        summary_rows.append((node_id, project_id, parsed.sha256, file_summary, "file", now))
        lexical_delete_ids.append((node_id,))
        lexical_rows.append(
            (node_id, Path(parsed.rel_path).name, parsed.rel_path, "", "", file_summary, "")
        )

    if file_node_rows:
        index.conn.executemany(
            """
            INSERT INTO nodes(
                id, project_id, parent_id, type, name, path,
                language, sha256, signature, docstring,
                start_line, end_line, source_hash,
                parse_status, parse_error, metadata, is_test, updated_at
            ) VALUES(?, ?, ?, 'file', ?, ?, ?, ?, NULL, NULL,
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

    symbol_by_name: Dict[str, List[str]] = defaultdict(list)
    node_to_file_node: Dict[str, str] = {}
    for row in index.conn.execute(
        """
        SELECT id, name, path FROM nodes
        WHERE project_id = ? AND type IN ('class','function','method')
        """,
        (project_id,),
    ):
        symbol_by_name[row["name"]].append(row["id"])
        if "." in row["name"]:
            symbol_by_name[row["name"].split(".")[-1]].append(row["id"])
        node_to_file_node[row["id"]] = file_node_id(row["path"])

    symbol_node_rows: List[tuple] = []
    for parsed in parsed_files:
        for symbol in parsed.symbols:
            parent = symbol.parent_symbol_id or file_node_id(parsed.rel_path)
            metadata = json.dumps(
                {"is_test": symbol.is_test, "bases": symbol.bases, "decorators": symbol.decorators},
                sort_keys=True,
            )
            symbol_node_rows.append(
                (
                    symbol.node_id, project_id, parent, symbol.kind, symbol.name, parsed.rel_path,
                    symbol.signature, symbol.docstring, symbol.start_line, symbol.end_line,
                    symbol.source_hash, metadata, 1 if symbol.is_test else 0, now,
                )
            )
            symbol_by_name[symbol.name].append(symbol.node_id)
            if "." in symbol.name:
                symbol_by_name[symbol.name.split(".")[-1]].append(symbol.node_id)
            node_to_file_node[symbol.node_id] = file_node_id(parsed.rel_path)

            summary = _symbol_summary(symbol)
            summary_rows.append((symbol.node_id, project_id, symbol.source_hash, summary, symbol.kind, now))
            lexical_delete_ids.append((symbol.node_id,))
            lexical_rows.append(
                (
                    symbol.node_id,
                    symbol.name,
                    parsed.rel_path,
                    symbol.signature or "",
                    symbol.docstring or "",
                    summary,
                    " ".join(code_tokenize(symbol.source)),
                )
            )

    if symbol_node_rows:
        index.conn.executemany(
            """
            INSERT INTO nodes(
                id, project_id, parent_id, type, name, path,
                language, sha256, signature, docstring,
                start_line, end_line, source_hash,
                parse_status, parse_error, metadata, is_test, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                parent_id = excluded.parent_id,
                type = excluded.type,
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

    if summary_rows:
        index.conn.executemany(
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
            summary_rows,
        )

    if lexical_delete_ids:
        index.conn.executemany(
            "DELETE FROM lexical_index WHERE node_id = ?",
            lexical_delete_ids,
        )
    if lexical_rows:
        index.conn.executemany(
            """
            INSERT INTO lexical_index(node_id, name, path, signature, docstring, summary, source)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            lexical_rows,
        )

    module_to_file_id = _module_to_file_id(index, project_id)
    edge_rows: List[tuple] = []
    for parsed in parsed_files:
        current_module = module_name_from_relpath(parsed.rel_path)
        current_file_id = file_node_id(parsed.rel_path)
        for symbol in parsed.symbols:
            source = symbol.parent_symbol_id if symbol.kind == "method" and symbol.parent_symbol_id else current_file_id
            edge_rows.append((project_id, source, symbol.node_id, "contains", None))

        for import_name in parsed.imports:
            target_file_id = resolve_local_import(import_name, module_to_file_id, current_module)
            if target_file_id:
                edge_rows.append(
                    (project_id, current_file_id, target_file_id, "imports", json_dumps({"import": import_name}))
                )

        for symbol in parsed.symbols:
            for call in symbol.calls:
                target = _pick_call_target(call, current_file_id, symbol_by_name, node_to_file_node)
                if target and target != symbol.node_id:
                    edge_rows.append(
                        (project_id, symbol.node_id, target, "calls", json_dumps({"symbol": call}))
                    )
            for base in symbol.bases:
                target = _pick_call_target(base, current_file_id, symbol_by_name, node_to_file_node)
                if target and target != symbol.node_id:
                    edge_rows.append(
                        (project_id, symbol.node_id, target, "inherits", json_dumps({"base": base}))
                    )
            for decorator in symbol.decorators:
                target = _pick_call_target(decorator, current_file_id, symbol_by_name, node_to_file_node)
                if target and target != symbol.node_id:
                    edge_rows.append(
                        (project_id, target, symbol.node_id, "decorates", json_dumps({"decorator": decorator}))
                    )
            if symbol.is_test:
                for call in symbol.calls:
                    target = _pick_call_target(call, current_file_id, symbol_by_name, node_to_file_node)
                    if target and target != symbol.node_id:
                        edge_rows.append(
                            (project_id, target, symbol.node_id, "tested_by", json_dumps({"via": call}))
                        )

    edges_before = int(
        index.conn.execute(
            "SELECT COUNT(*) AS c FROM edges WHERE project_id = ?", (project_id,)
        ).fetchone()["c"]
    )
    if edge_rows:
        index.conn.executemany(
            """
            INSERT OR IGNORE INTO edges(project_id, source_node_id, target_node_id, relation, metadata)
            VALUES(?, ?, ?, ?, ?)
            """,
            edge_rows,
        )
    index.conn.commit()

    total_edges = int(
        index.conn.execute(
            "SELECT COUNT(*) AS c FROM edges WHERE project_id = ?", (project_id,)
        ).fetchone()["c"]
    )
    return {
        "symbols": sum(len(parsed.symbols) for parsed in parsed_files),
        "edges": total_edges if len(parsed_files) > 1 else (total_edges - edges_before),
    }


def _write_repo_and_folders(
    index: ProjectIndex,
    project_id: int,
    repo_root: str,
    parsed_files: Sequence[ParsedFile],
    now: float,
) -> None:
    repo_name = Path(repo_root).name or "repo"
    repo_id = repo_node_id(repo_name)
    index.conn.execute(
        """
        INSERT OR IGNORE INTO nodes(
            id, project_id, parent_id, type, name, path,
            language, sha256, signature, docstring,
            start_line, end_line, source_hash,
            parse_status, parse_error, metadata, updated_at
        ) VALUES(?, ?, NULL, 'repo', ?, '', NULL, NULL, NULL, NULL,
                 NULL, NULL, '', NULL, NULL, NULL, ?)
        """,
        (repo_id, project_id, repo_name, now),
    )

    folder_paths: set[str] = set()
    for parsed in parsed_files:
        parts = parsed.rel_path.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            folder_paths.add("/".join(parts[:i]))

    for rel_dir in sorted(folder_paths, key=lambda p: p.count("/")):
        parent_parts = rel_dir.split("/")[:-1]
        parent = folder_node_id("/".join(parent_parts)) if parent_parts else repo_id
        index.conn.execute(
            """
            INSERT OR IGNORE INTO nodes(
                id, project_id, parent_id, type, name, path,
                language, sha256, signature, docstring,
                start_line, end_line, source_hash,
                parse_status, parse_error, metadata, updated_at
            ) VALUES(?, ?, ?, 'folder', ?, ?, NULL, NULL, NULL, NULL,
                     NULL, NULL, '', NULL, NULL, NULL, ?)
            """,
            (
                folder_node_id(rel_dir),
                project_id,
                parent,
                rel_dir.rsplit("/", 1)[-1],
                rel_dir,
                now,
            ),
        )


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


def _module_to_file_id(index: ProjectIndex, project_id: int) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for row in index.conn.execute(
        "SELECT path FROM nodes WHERE project_id = ? AND type = 'file'",
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
