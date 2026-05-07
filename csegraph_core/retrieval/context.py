from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from csegraph_core.config.profiles import get_profile
from csegraph_core.core.models import ContextNode, ContextResult
from csegraph_core.cse.metrics import (
    CONFIDENCE_THRESHOLD,
    DEP_THRESHOLD,
    ENTITY_THRESHOLD,
    SEMANTIC_THRESHOLD,
    all_pass,
    compute_metrics,
    raw_code_nodes,
)
from csegraph_core.index.loaders import edge_maps, load_edges, load_summaries, load_symbols
from csegraph_core.index.repository import ProjectIndex
from csegraph_core.retrieval.scoring import apply_graph_expansion, fts_lexical_scores, lexical_scores


class ContextService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def build_context(
        self,
        task: str,
        target: Optional[str] = None,
        profile: str = "small",
        include_source: str = "auto",
        max_tokens: Optional[int] = None,
    ) -> ContextResult:
        config = get_profile(profile)
        include_source = _validate_include_source(include_source)
        if max_tokens is not None and max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer when provided.")
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            project = index.get_project()
            project_id = int(project["id"])
            repo_root = project["root_dir"]

            symbols = load_symbols(index, project_id)
            summaries = load_summaries(index, project_id)
            edges = load_edges(index, project_id)
            outgoing, _incoming = edge_maps(edges)

            if not symbols:
                raise ValueError("No symbols are indexed in this database.")

            target_node_id = _resolve_target(target, task, symbols, summaries, index, project_id)
            fts_seed = fts_lexical_scores(index.conn, project_id, task)
            scores, evidence = lexical_scores(task, symbols, summaries, fts_seed=fts_seed)
            if target_node_id:
                scores[target_node_id] += 4.0
                evidence[target_node_id].append("target")

            anchors = [target_node_id] if target_node_id else [
                node_id for node_id, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)[: config.top_k]
            ]
            for anchor in anchors:
                apply_graph_expansion(
                    anchor,
                    config.graph_radius,
                    scores,
                    evidence,
                    index.conn,
                    project_id,
                    symbols,
                )

            context_ids = _select_context_ids(
                target_node_id,
                scores,
                outgoing,
                config.context_budget,
            )
            metrics = compute_metrics(task, target_node_id, context_ids, symbols, summaries, outgoing)
            is_sufficient = all_pass(metrics)
            raw_nodes = raw_code_nodes(
                target_node_id,
                context_ids,
                outgoing,
                metrics,
                config.raw_code_budget,
            )
            source_ids = _source_candidate_ids(
                include_source,
                target_node_id,
                context_ids,
                outgoing,
                symbols,
                raw_nodes,
            )

            context_nodes: List[ContextNode] = []
            for node_id in context_ids:
                row = symbols[node_id]
                raw_evidence = evidence.get(node_id, [])
                lineage = sorted({e for e in raw_evidence if e.startswith("expanded-from-")})
                clean_evidence = sorted({e for e in raw_evidence if not e.startswith("expanded-from-")})
                summary = summaries.get(node_id, "")
                source_text = (
                    _read_node_source(repo_root, row) if node_id in source_ids else None
                )
                estimated_tokens = _estimate_node_tokens(row, summary, source_text)
                context_nodes.append(
                    ContextNode(
                        node_id=node_id,
                        kind=row["kind"],
                        name=row["name"],
                        file_path=row["file_path"],
                        start_line=row["start_line"],
                        end_line=row["end_line"],
                        score=round(scores.get(node_id, 0.0), 4),
                        raw_code=node_id in raw_nodes and source_text is not None,
                        evidence=clean_evidence,
                        summary=summary,
                        lineage=lineage,
                        source_text=source_text,
                        estimated_tokens=estimated_tokens,
                    )
                )

            context_nodes = _apply_token_budget(context_nodes, max_tokens)
            context_ids = [node.node_id for node in context_nodes]
            metrics = compute_metrics(task, target_node_id, context_ids, symbols, summaries, outgoing)
            is_sufficient = all_pass(metrics)
            raw_nodes = {node.node_id for node in context_nodes if node.raw_code}
            estimated_tokens = sum(node.estimated_tokens for node in context_nodes)

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
                        "evidence": list(node.evidence) + list(node.lineage),
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
                raw_code_nodes=sorted(raw_nodes),
                thresholds={
                    "dependency_completeness": DEP_THRESHOLD,
                    "entity_coverage": ENTITY_THRESHOLD,
                    "semantic_overlap": SEMANTIC_THRESHOLD,
                    "model_confidence": CONFIDENCE_THRESHOLD,
                },
                run_id=run_id,
                estimated_tokens=estimated_tokens,
            )
        finally:
            index.close()


def _resolve_target(
    target: Optional[str],
    task: str,
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
    index: Optional[ProjectIndex] = None,
    project_id: Optional[int] = None,
) -> str:
    if target:
        if target in symbols:
            return target
        if index is not None and project_id is not None:
            lowered = target.lower()
            row = index.conn.execute(
                """
                SELECT id FROM nodes
                 WHERE project_id = ?
                   AND type IN ('class','function','method')
                   AND (LOWER(name) = ? OR LOWER(path) = ?)
                 ORDER BY (LOWER(name) = ?) DESC, length(name) ASC
                 LIMIT 1
                """,
                (project_id, lowered, lowered, lowered),
            ).fetchone()
            if row is not None:
                return row["id"]
            row = index.conn.execute(
                """
                SELECT id FROM nodes
                 WHERE project_id = ?
                   AND type IN ('class','function','method')
                   AND (LOWER(name) LIKE ? OR LOWER(path) LIKE ?)
                 ORDER BY length(name) ASC
                 LIMIT 1
                """,
                (project_id, f"%{lowered}%", f"%{lowered}%"),
            ).fetchone()
            if row is not None:
                return row["id"]
        raise ValueError(f"Target '{target}' did not match any indexed symbol.")
    scores, _ = lexical_scores(task, symbols, summaries, fts_seed=None)
    return max(scores.items(), key=lambda item: item[1])[0]


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


def _validate_include_source(value: str) -> str:
    valid = {"auto", "always", "never"}
    if value not in valid:
        choices = ", ".join(sorted(valid))
        raise ValueError(f"include_source must be one of: {choices}")
    return value


def _source_candidate_ids(
    include_source: str,
    target_node_id: str,
    context_ids: Sequence[str],
    outgoing: Dict[str, List[Dict[str, Any]]],
    symbols: Dict[str, Dict[str, Any]],
    raw_nodes: Sequence[str],
) -> set[str]:
    context_set = set(context_ids)
    if include_source == "never":
        return set()
    if include_source == "always":
        return context_set

    direct_calls = {
        edge["target_id"]
        for edge in outgoing.get(target_node_id, [])
        if edge["relation"] == "calls" and edge["target_id"] in context_set
    }
    small_helpers = {
        node_id
        for node_id in context_set
        if _line_count(symbols.get(node_id, {})) <= 12
        and symbols.get(node_id, {}).get("kind") in {"function", "method"}
    }
    return ({target_node_id} | set(raw_nodes) | direct_calls | small_helpers) & context_set


def _line_count(row: Dict[str, Any]) -> int:
    start = row.get("start_line")
    end = row.get("end_line")
    if start is None or end is None:
        return 0
    return max(0, int(end) - int(start) + 1)


def _read_node_source(repo_root: str, row: Dict[str, Any]) -> Optional[str]:
    file_path = row.get("file_path")
    start_line = row.get("start_line")
    end_line = row.get("end_line")
    if not file_path or start_line is None or end_line is None:
        return None
    path = Path(repo_root) / str(file_path)
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    start = max(0, int(start_line) - 1)
    end = min(len(lines), int(end_line))
    return "\n".join(lines[start:end]) + ("\n" if end > start else "")


def _estimate_node_tokens(
    row: Dict[str, Any],
    summary: str,
    source_text: Optional[str],
) -> int:
    if source_text is not None:
        return _estimate_tokens(source_text)
    fallback = " ".join(
        value
        for value in (
            str(row.get("name") or ""),
            str(row.get("file_path") or ""),
            str(row.get("signature") or ""),
            str(row.get("docstring") or ""),
            summary,
        )
        if value
    )
    return _estimate_tokens(fallback)


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def _apply_token_budget(
    nodes: List[ContextNode],
    max_tokens: Optional[int],
) -> List[ContextNode]:
    if max_tokens is None:
        return nodes

    selected: List[ContextNode] = []
    used = 0
    for node in nodes:
        candidate = node
        if used + candidate.estimated_tokens > max_tokens and candidate.source_text is not None:
            candidate = ContextNode(
                node_id=node.node_id,
                kind=node.kind,
                name=node.name,
                file_path=node.file_path,
                start_line=node.start_line,
                end_line=node.end_line,
                score=node.score,
                raw_code=False,
                evidence=node.evidence,
                summary=node.summary,
                lineage=node.lineage,
                source_text=None,
                estimated_tokens=_estimate_tokens(
                    " ".join(
                        value
                        for value in (node.name, node.file_path, node.summary)
                        if value
                    )
                ),
            )
        if used + candidate.estimated_tokens <= max_tokens:
            selected.append(candidate)
            used += candidate.estimated_tokens
    return selected
