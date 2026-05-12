from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from csegraph_core.config.profiles import load_profile
from csegraph_core.core.models import ContextNode, ContextResult, SufficiencyResult
from csegraph_core.cse.metrics import (
    all_pass,
    compute_metrics,
    raw_code_nodes,
)
from csegraph_core.index.loaders import edge_maps, load_edges, load_summaries, load_symbols
from csegraph_core.index.repository import ProjectIndex
from csegraph_core.retrieval.explain import build_explanation, normalize_reasons
from csegraph_core.retrieval.scoring import apply_graph_expansion, fts_lexical_scores, lexical_scores
from csegraph_core.text.source_reader import read_source_lines


class ContextService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def build_context(
        self,
        task: str,
        target: Optional[str] = None,
        profile: Optional[str] = None,
        include_source: str = "auto",
        max_tokens: Optional[int] = None,
        explain: bool = False,
        config_path: Optional[str] = None,
    ) -> ContextResult:
        include_source = _validate_include_source(include_source)
        if max_tokens is not None and max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer when provided.")
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]
            config = load_profile(profile, config_path=config_path, repo_root=repo_root)

            symbols = load_symbols(index)
            summaries = load_summaries(index)
            edges = load_edges(index)
            outgoing, incoming = edge_maps(edges)

            if not symbols:
                raise ValueError("No symbols are indexed in this database.")

            target_id = _resolve_target(target, task, symbols, summaries, index)
            fts_seed = fts_lexical_scores(index.conn, task)
            scores, evidence = lexical_scores(task, symbols, summaries, fts_seed=fts_seed)
            if target_id:
                scores[target_id] += 4.0
                evidence[target_id].append("target")

            anchors = [target_id] if target_id else [
                node_id for node_id, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)[: config.top_k]
            ]
            for anchor in anchors:
                apply_graph_expansion(
                    anchor,
                    config.graph_radius,
                    scores,
                    evidence,
                    index.conn,
                    symbols,
                )

            context_ids = _select_context_ids(
                target_id,
                scores,
                outgoing,
                config.context_budget,
            )
            metrics = compute_metrics(task, target_id, context_ids, symbols, summaries, outgoing)
            sufficient = all_pass(
                metrics,
                dep_threshold=config.dep_threshold,
                entity_threshold=config.entity_threshold,
                semantic_threshold=config.semantic_threshold,
                semantic_threshold_relaxed=config.semantic_threshold_relaxed,
                confidence_threshold=config.confidence_threshold,
            )
            raw_nodes = raw_code_nodes(
                target_id,
                context_ids,
                outgoing,
                metrics,
                config.raw_code_budget,
                confidence_threshold=config.confidence_threshold,
            )
            source_ids = _source_candidate_ids(
                include_source,
                target_id,
                context_ids,
                outgoing,
                symbols,
                raw_nodes,
            )

            nodes: List[ContextNode] = []
            target_row = symbols.get(target_id, {})
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
                reason = normalize_reasons(
                    node_id=node_id,
                    target_id=target_id,
                    row=row,
                    target_row=target_row,
                    evidence=clean_evidence,
                    lineage=lineage,
                    outgoing=outgoing,
                    incoming=incoming,
                    symbols=symbols,
                    raw_nodes=raw_nodes,
                )
                nodes.append(
                    ContextNode(
                        id=node_id,
                        kind=row["kind"],
                        name=row["name"],
                        path=row["file_path"],
                        line_range=_line_range(row["start_line"], row["end_line"]),
                        score=round(scores.get(node_id, 0.0), 4),
                        language=row["language"],
                        raw_code=node_id in raw_nodes and source_text is not None,
                        evidence=clean_evidence,
                        summary=summary,
                        lineage=lineage,
                        source_text=source_text,
                        estimated_tokens=estimated_tokens,
                        reason=reason,
                        explanation=build_explanation(reason) if explain else None,
                    )
                )

            nodes = _apply_token_budget(nodes, max_tokens)
            context_ids = [node.id for node in nodes]
            metrics = compute_metrics(task, target_id, context_ids, symbols, summaries, outgoing)
            sufficient = all_pass(
                metrics,
                dep_threshold=config.dep_threshold,
                entity_threshold=config.entity_threshold,
                semantic_threshold=config.semantic_threshold,
                semantic_threshold_relaxed=config.semantic_threshold_relaxed,
                confidence_threshold=config.confidence_threshold,
            )
            raw_nodes = {node.id for node in nodes if node.raw_code}
            estimated_tokens = sum(node.estimated_tokens for node in nodes)

            run_id = index.insert_retrieval_run(
                query=task,
                target=target_id,
                profile=config.name,
                metrics={
                    "dependency_completeness": metrics.dependency_completeness,
                    "entity_coverage": metrics.entity_coverage,
                    "semantic_overlap": metrics.semantic_overlap,
                    "model_confidence": metrics.model_confidence,
                },
                sufficient=sufficient,
            )
            index.insert_retrieval_context(
                run_id,
                [
                    {
                        "node_id": node.id,
                        "rank": rank,
                        "score": node.score,
                        "raw_code": node.raw_code,
                        "evidence": list(node.evidence) + list(node.lineage),
                    }
                    for rank, node in enumerate(nodes, start=1)
                ],
            )

            return ContextResult(
                command="context",
                db_path=self.db_path,
                repo_root=repo_root,
                profile=config.name,
                query=task,
                target=target_id,
                sufficiency=SufficiencyResult(
                    sufficient=sufficient,
                    metrics=metrics,
                    thresholds={
                        "dependency_completeness": config.dep_threshold,
                        "entity_coverage": config.entity_threshold,
                        "semantic_overlap": config.semantic_threshold,
                        "semantic_overlap_relaxed": config.semantic_threshold_relaxed,
                        "model_confidence": config.confidence_threshold,
                    },
                ),
                total_estimated_tokens=estimated_tokens,
                nodes=nodes,
                raw_code_nodes=sorted(raw_nodes),
                run_id=run_id,
            )
        finally:
            index.close()


def _resolve_target(
    target: Optional[str],
    task: str,
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
    index: Optional[ProjectIndex] = None,
) -> str:
    if target:
        if target in symbols:
            return target
        if index is not None:
            lowered = target.lower()
            row = index.conn.execute(
                """
                SELECT id FROM nodes
                 WHERE type IN ('class','function','method')
                   AND (LOWER(name) = ? OR LOWER(path) = ?)
                 ORDER BY (LOWER(name) = ?) DESC, length(name) ASC
                 LIMIT 1
                """,
                (lowered, lowered, lowered),
            ).fetchone()
            if row is not None:
                return row["id"]
            row = index.conn.execute(
                """
                SELECT id FROM nodes
                 WHERE type IN ('class','function','method')
                   AND (LOWER(name) LIKE ? OR LOWER(path) LIKE ?)
                 ORDER BY length(name) ASC
                 LIMIT 1
                """,
                (f"%{lowered}%", f"%{lowered}%"),
            ).fetchone()
            if row is not None:
                return row["id"]
        raise ValueError(f"Target '{target}' did not match any indexed symbol.")
    scores, _ = lexical_scores(task, symbols, summaries, fts_seed=None)
    return max(scores.items(), key=lambda item: item[1])[0]


def _select_context_ids(
    target_id: str,
    scores: Dict[str, float],
    outgoing: Dict[str, List[Dict[str, Any]]],
    budget: int,
) -> List[str]:
    required = [target_id]
    for edge in outgoing.get(target_id, []):
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
    target_id: str,
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
        for edge in outgoing.get(target_id, [])
        if edge["relation"] == "calls" and edge["target_id"] in context_set
    }
    small_helpers = {
        node_id
        for node_id in context_set
        if _line_count(symbols.get(node_id, {})) <= 12
        and symbols.get(node_id, {}).get("kind") in {"function", "method"}
    }
    return ({target_id} | set(raw_nodes) | direct_calls | small_helpers) & context_set


def _line_count(row: Dict[str, Any]) -> int:
    start = row.get("start_line")
    end = row.get("end_line")
    if start is None or end is None:
        return 0
    return max(0, int(end) - int(start) + 1)


def _line_range(start_line: Optional[int], end_line: Optional[int]) -> Optional[List[int]]:
    if start_line is None or end_line is None:
        return None
    return [int(start_line), int(end_line)]


def _read_node_source(repo_root: str, row: Dict[str, Any]) -> Optional[str]:
    file_path = row.get("file_path")
    start_line = row.get("start_line")
    end_line = row.get("end_line")
    if not file_path or start_line is None or end_line is None:
        return None
    return read_source_lines(repo_root, str(file_path), int(start_line), int(end_line))


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
            reason = [item for item in node.reason if item != "raw_code_fallback"]
            compact_reason = reason or list(node.reason)
            candidate = ContextNode(
                id=node.id,
                kind=node.kind,
                name=node.name,
                path=node.path,
                line_range=node.line_range,
                score=node.score,
                language=node.language,
                raw_code=False,
                evidence=node.evidence,
                summary=node.summary,
                lineage=node.lineage,
                source_text=None,
                estimated_tokens=_estimate_tokens(
                    " ".join(
                        value
                        for value in (node.name, node.path, node.summary)
                        if value
                    )
                ),
                reason=compact_reason,
                explanation=build_explanation(compact_reason) if node.explanation is not None else None,
            )
        if used + candidate.estimated_tokens <= max_tokens:
            selected.append(candidate)
            used += candidate.estimated_tokens
    return selected
