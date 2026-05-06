from __future__ import annotations

import json
import math
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from csegraph.models import (
    ContextNode,
    ContextResult,
    GraphEdgeView,
    GraphNodeView,
    GraphResult,
    IndexResult,
    RefreshResult,
    SufficiencyMetrics,
)
from csegraph.parser import (
    ParsedFile,
    ParsedSymbol,
    code_tokenize,
    extract_query_entities,
    file_node_id,
    iter_python_files,
    module_name_from_relpath,
    parse_python_file,
    resolve_local_import,
)
from csegraph.profiles import get_profile
from csegraph.sqlite_index import ProjectIndex, json_dumps, json_loads


DEP_THRESHOLD = 0.80
ENTITY_THRESHOLD = 0.80
SEMANTIC_THRESHOLD = 0.50
SEMANTIC_THRESHOLD_RELAXED = 0.0
CONFIDENCE_THRESHOLD = 0.70


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


class ContextService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def build_context(
        self,
        task: str,
        target: Optional[str] = None,
        profile: str = "small",
    ) -> ContextResult:
        config = get_profile(profile)
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            project = index.get_project()
            project_id = int(project["id"])
            repo_root = project["root_dir"]

            symbols = _load_symbols(index, project_id)
            summaries = _load_summaries(index, project_id)
            edges = _load_edges(index, project_id)
            outgoing, incoming = _edge_maps(edges)

            if not symbols:
                raise ValueError("No symbols are indexed in this database.")

            target_node_id = _resolve_target(target, task, symbols, summaries)
            scores, evidence = _lexical_scores(task, symbols, summaries)
            if target_node_id:
                scores[target_node_id] += 4.0
                evidence[target_node_id].append("target")

            anchors = [target_node_id] if target_node_id else [
                node_id for node_id, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)[: config.top_k]
            ]
            for anchor in anchors:
                _apply_graph_expansion(
                    anchor,
                    config.graph_radius,
                    scores,
                    evidence,
                    outgoing,
                    incoming,
                    symbols,
                )

            context_ids = _select_context_ids(
                target_node_id,
                scores,
                outgoing,
                config.context_budget,
            )
            metrics = _compute_metrics(task, target_node_id, context_ids, symbols, summaries, outgoing)
            is_sufficient = _all_pass(metrics)
            raw_code_nodes = _raw_code_nodes(
                target_node_id,
                context_ids,
                outgoing,
                metrics,
                config.raw_code_budget,
            )

            context_nodes: List[ContextNode] = []
            for node_id in context_ids:
                row = symbols[node_id]
                context_nodes.append(
                    ContextNode(
                        node_id=node_id,
                        kind=row["kind"],
                        name=row["name"],
                        file_path=row["file_path"],
                        start_line=row["start_line"],
                        end_line=row["end_line"],
                        score=round(scores.get(node_id, 0.0), 4),
                        raw_code=node_id in raw_code_nodes,
                        evidence=sorted(set(evidence.get(node_id, []))),
                        summary=summaries.get(node_id, ""),
                    )
                )

            run_id = index.insert_retrieval_run(
                project_id=project_id,
                query_text=task,
                target_node_id=target_node_id,
                profile=config.name,
                metrics={
                    "dependency_completeness": metrics.dependency_completeness,
                    "entity_coverage": metrics.entity_coverage,
                    "semantic_overlap": metrics.semantic_overlap,
                    "model_confidence": metrics.model_confidence,
                },
                is_sufficient=is_sufficient,
            )
            index.insert_retrieval_context(
                run_id,
                [
                    {
                        "node_id": node.node_id,
                        "rank": rank,
                        "score": node.score,
                        "raw_code": node.raw_code,
                        "evidence": node.evidence,
                    }
                    for rank, node in enumerate(context_nodes, start=1)
                ],
            )

            return ContextResult(
                command="context",
                db_path=self.db_path,
                repo_root=repo_root,
                profile=config.name,
                task=task,
                target_node_id=target_node_id,
                is_sufficient=is_sufficient,
                metrics=metrics,
                context_nodes=context_nodes,
                raw_code_nodes=sorted(raw_code_nodes),
                thresholds={
                    "dependency_completeness": DEP_THRESHOLD,
                    "entity_coverage": ENTITY_THRESHOLD,
                    "semantic_overlap": SEMANTIC_THRESHOLD,
                    "model_confidence": CONFIDENCE_THRESHOLD,
                },
                run_id=run_id,
            )
        finally:
            index.close()


class GraphQueryService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def neighborhood(self, node_id: str, depth: int = 1) -> GraphResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            project = index.get_project()
            project_id = int(project["id"])
            repo_root = project["root_dir"]
            symbols = _load_symbols(index, project_id)
            files = _load_files(index, project_id)
            edges = _load_edges(index, project_id)
            outgoing, incoming = _edge_maps(edges)

            resolved = _resolve_graph_node(node_id, symbols, files)
            visited = {resolved}
            queue: deque[Tuple[str, int]] = deque([(resolved, 0)])
            selected_edges: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}

            while queue:
                current, current_depth = queue.popleft()
                if current_depth >= depth:
                    continue
                for edge in outgoing.get(current, []) + incoming.get(current, []):
                    key = (
                        edge["source_id"],
                        edge["target_id"],
                        edge["relation"],
                        edge.get("metadata") or "",
                    )
                    selected_edges[key] = edge
                    neighbor = edge["target_id"] if edge["source_id"] == current else edge["source_id"]
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, current_depth + 1))

            nodes = [_node_view(node, symbols, files) for node in sorted(visited)]
            graph_edges = [
                GraphEdgeView(
                    source=edge["source_id"],
                    target=edge["target_id"],
                    relation=edge["relation"],
                    metadata=json_loads(edge.get("metadata")),
                )
                for edge in sorted(
                    selected_edges.values(),
                    key=lambda item: (item["source_id"], item["relation"], item["target_id"]),
                )
            ]
            return GraphResult(
                command="graph",
                db_path=self.db_path,
                repo_root=repo_root,
                node_id=resolved,
                depth=depth,
                nodes=nodes,
                edges=graph_edges,
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
        cur = index.conn.execute(
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


def _load_files(index: ProjectIndex, project_id: int) -> Dict[str, Dict[str, Any]]:
    files: Dict[str, Dict[str, Any]] = {}
    for row in index.conn.execute(
        "SELECT * FROM files WHERE project_id = ?",
        (project_id,),
    ):
        files[file_node_id(row["path"])] = dict(row)
    return files


def _load_symbols(index: ProjectIndex, project_id: int) -> Dict[str, Dict[str, Any]]:
    symbols: Dict[str, Dict[str, Any]] = {}
    for row in index.conn.execute(
        """
        SELECT s.*, f.path AS file_path
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.project_id = ?
        """,
        (project_id,),
    ):
        symbols[row["id"]] = dict(row)
    return symbols


def _load_summaries(index: ProjectIndex, project_id: int) -> Dict[str, str]:
    return {
        row["node_id"]: row["summary"]
        for row in index.conn.execute(
            "SELECT node_id, summary FROM summaries WHERE project_id = ?",
            (project_id,),
        )
    }


def _load_edges(index: ProjectIndex, project_id: int) -> List[Dict[str, Any]]:
    return [
        dict(row)
        for row in index.conn.execute(
            "SELECT * FROM edges WHERE project_id = ?",
            (project_id,),
        )
    ]


def _edge_maps(edges: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
    outgoing: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    incoming: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        outgoing[edge["source_id"]].append(edge)
        incoming[edge["target_id"]].append(edge)
    return outgoing, incoming


def _resolve_target(
    target: Optional[str],
    task: str,
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
) -> str:
    if target:
        if target in symbols:
            return target
        lowered = target.lower()
        for node_id, row in symbols.items():
            if row["name"].lower() == lowered:
                return node_id
        for node_id, row in symbols.items():
            if row["file_path"].lower() == lowered:
                return node_id
        for node_id, row in symbols.items():
            if lowered in row["name"].lower() or lowered in row["file_path"].lower():
                return node_id
        raise ValueError(f"Target '{target}' did not match any indexed symbol.")
    scores, _ = _lexical_scores(task, symbols, summaries)
    return max(scores.items(), key=lambda item: item[1])[0]


def _lexical_scores(
    task: str,
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
) -> Tuple[Dict[str, float], Dict[str, List[str]]]:
    task_tokens = set(code_tokenize(task))
    scores: Dict[str, float] = defaultdict(float)
    evidence: Dict[str, List[str]] = defaultdict(list)
    task_lower = task.lower()
    for node_id, row in symbols.items():
        content = " ".join(
            [
                row["name"],
                row["file_path"],
                row.get("signature") or "",
                row.get("docstring") or "",
                summaries.get(node_id, ""),
            ]
        )
        content_tokens = set(code_tokenize(content))
        overlap = task_tokens & content_tokens
        if overlap:
            scores[node_id] += float(len(overlap))
            evidence[node_id].append("lexical-token-overlap")
        if row["name"].lower() in task_lower:
            scores[node_id] += 3.0
            evidence[node_id].append("exact-symbol-name")
        if row["file_path"].lower() in task_lower:
            scores[node_id] += 1.5
            evidence[node_id].append("file-path-match")
        scores[node_id] += 0.01
    return scores, evidence


def _apply_graph_expansion(
    anchor: str,
    radius: int,
    scores: Dict[str, float],
    evidence: Dict[str, List[str]],
    outgoing: Dict[str, List[Dict[str, Any]]],
    incoming: Dict[str, List[Dict[str, Any]]],
    symbols: Dict[str, Dict[str, Any]],
) -> None:
    relation_weight = {"calls": 2.5, "imports": 0.8, "contains": 0.4}
    queue: deque[Tuple[str, int]] = deque([(anchor, 0)])
    visited = {anchor}
    while queue:
        current, depth = queue.popleft()
        if depth >= radius:
            continue
        for edge in outgoing.get(current, []) + incoming.get(current, []):
            neighbor = edge["target_id"] if edge["source_id"] == current else edge["source_id"]
            if neighbor not in symbols:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, depth + 1))
                continue
            boost = relation_weight.get(edge["relation"], 0.2) / (depth + 1)
            scores[neighbor] += boost
            evidence[neighbor].append(f"graph-{edge['relation']}")
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, depth + 1))


def _select_context_ids(
    target_node_id: str,
    scores: Dict[str, float],
    outgoing: Dict[str, List[Dict[str, Any]]],
    budget: int,
) -> List[str]:
    required = [target_node_id]
    for edge in outgoing.get(target_node_id, []):
        if edge["relation"] == "calls":
            required.append(edge["target_id"])
    selected: List[str] = []
    for node_id in required:
        if node_id in scores and node_id not in selected and len(selected) < budget:
            selected.append(node_id)
    for node_id, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True):
        if len(selected) >= budget:
            break
        if node_id not in selected:
            selected.append(node_id)
    return selected


def _compute_metrics(
    task: str,
    target_node_id: str,
    context_ids: Sequence[str],
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
    outgoing: Dict[str, List[Dict[str, Any]]],
) -> SufficiencyMetrics:
    context_set = set(context_ids)
    direct_calls = {
        edge["target_id"]
        for edge in outgoing.get(target_node_id, [])
        if edge["relation"] == "calls" and edge["target_id"] in symbols
    }
    dep = 1.0 if not direct_calls else len(direct_calls & context_set) / len(direct_calls)

    names = [row["name"] for row in symbols.values()]
    entities = extract_query_entities(task, names)
    context_names = {symbols[node_id]["name"] for node_id in context_set if node_id in symbols}
    ent = 1.0 if not entities else len(entities & context_names) / len(entities)

    task_tokens = set(code_tokenize(task))
    context_tokens: Set[str] = set()
    for node_id in context_set:
        if node_id not in symbols:
            continue
        row = symbols[node_id]
        context_tokens.update(
            code_tokenize(
                " ".join(
                    [
                        row["name"],
                        row["file_path"],
                        row.get("signature") or "",
                        row.get("docstring") or "",
                        summaries.get(node_id, ""),
                    ]
                )
            )
        )
    if not task_tokens or not context_tokens:
        sem = 0.0
    else:
        sem = len(task_tokens & context_tokens) / len(task_tokens | context_tokens)

    conf = min(1.0, max(0.0, 0.45 * dep + 0.35 * ent + 0.20 * sem))
    return SufficiencyMetrics(
        dependency_completeness=round(dep, 4),
        entity_coverage=round(ent, 4),
        semantic_overlap=round(sem, 4),
        model_confidence=round(conf, 4),
    )


def _all_pass(metrics: SufficiencyMetrics) -> bool:
    structural_ok = (
        metrics.dependency_completeness >= DEP_THRESHOLD
        and metrics.entity_coverage >= ENTITY_THRESHOLD
    )
    sem_threshold = SEMANTIC_THRESHOLD_RELAXED if structural_ok else SEMANTIC_THRESHOLD
    return (
        structural_ok
        and metrics.semantic_overlap >= sem_threshold
        and metrics.model_confidence >= CONFIDENCE_THRESHOLD
    )


def _raw_code_nodes(
    target_node_id: str,
    context_ids: Sequence[str],
    outgoing: Dict[str, List[Dict[str, Any]]],
    metrics: SufficiencyMetrics,
    budget: int,
) -> Set[str]:
    if metrics.model_confidence >= CONFIDENCE_THRESHOLD:
        return set()
    raw: List[str] = []
    for edge in outgoing.get(target_node_id, []):
        if edge["relation"] == "calls" and edge["target_id"] in context_ids:
            raw.append(edge["target_id"])
    return set(raw[:budget])


def _resolve_graph_node(
    node: str,
    symbols: Dict[str, Dict[str, Any]],
    files: Dict[str, Dict[str, Any]],
) -> str:
    if node in symbols or node in files:
        return node
    lowered = node.lower()
    for node_id, row in symbols.items():
        if row["name"].lower() == lowered:
            return node_id
    for node_id, row in files.items():
        if row["path"].lower() == lowered:
            return node_id
    raise ValueError(f"Node '{node}' did not match any indexed file or symbol.")


def _node_view(
    node_id: str,
    symbols: Dict[str, Dict[str, Any]],
    files: Dict[str, Dict[str, Any]],
) -> GraphNodeView:
    if node_id in symbols:
        row = symbols[node_id]
        return GraphNodeView(
            node_id=node_id,
            kind=row["kind"],
            name=row["name"],
            file_path=row["file_path"],
            start_line=row["start_line"],
            end_line=row["end_line"],
        )
    row = files[node_id]
    return GraphNodeView(
        node_id=node_id,
        kind="file",
        name=Path(row["path"]).name,
        file_path=row["path"],
    )
