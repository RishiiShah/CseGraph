from __future__ import annotations

import copy
import itertools
import math
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from csegraph._core.config.profiles import load_profile
from csegraph._core.core.ids import file_node_id
from csegraph._core.core.models import (
    ContextNode,
    ContextRelationship,
    ContextResult,
    ImportPrelude,
    RelationshipOccurrence,
    SufficiencyResult,
)
from csegraph._core.cse.metrics import (
    SufficiencyMetrics,
    all_pass,
    compute_metrics,
    raw_code_nodes,
)
from csegraph._core.index.loaders import load_symbols
from csegraph._core.index.repository import ProjectIndex, json_loads
from csegraph._core.retrieval.explain import (
    build_explanation,
    build_reason_details,
    normalize_reasons,
)
from csegraph._core.retrieval.helpers import is_small_helper_row
from csegraph._core.retrieval.scoring import (
    apply_graph_expansion_from_maps,
    fts_lexical_scores,
    lexical_scores,
)
from csegraph._core.retrieval.target_resolution import TargetResolution, resolve_target
from csegraph._core.text.entities import extract_query_entities
from csegraph._core.text.source_reader import read_source_lines

DETAIL_LEVELS = {"auto", "minimal", "standard", "full"}
MINIMAL_NODE_LIMIT = 5
MINIMAL_SUMMARY_CHAR_LIMIT = 240
DIRECT_CALL_ALWAYS_LIMIT = 2
DIRECT_CALL_SCORE_FLOOR = 4.0
DIRECT_CALLEE_PATH_LIMIT = 9
RANKED_SCORE_FLOOR = 3.0
RANKED_SCORE_RATIO_FLOOR = 0.30
IMPORT_PRELUDE_FILE_LIMIT = 8
IMPORT_PRELUDE_TOTAL_LINE_LIMIT = 40
IMPORT_PRELUDE_TOTAL_CHAR_LIMIT = 4000
OCCURRENCE_LIMIT_BY_PROFILE = {"small": 24, "medium": 48, "large": 72}
OCCURRENCE_PER_RELATIONSHIP_LIMIT = 2


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
        detail_level: str = "auto",
    ) -> ContextResult:
        include_source = _validate_include_source(include_source)
        detail_level = _validate_detail_level(detail_level)
        if max_tokens is not None and max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer when provided.")
        timings: Dict[str, float] = {}
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]
            config = load_profile(
                profile,
                config_path=config_path,
                repo_root=repo_root,
                source_file_count=_indexed_file_count(index),
            )

            from csegraph._core.retrieval.cache import CACHE
            
            t0 = time.perf_counter()
            snapshot = CACHE.get_snapshot(index)
            symbols = snapshot.get_symbols_light()
            summaries = snapshot.get_summaries()
            outgoing, incoming = snapshot.get_edge_maps()
            timings["load_data"] = _elapsed_ms(t0)

            if not symbols:
                raise ValueError("No symbols are indexed in this database.")

            t0 = time.perf_counter()
            resolution = resolve_target(
                target, task, symbols, summaries, index, repo_root=repo_root
            )
            timings["target_resolution"] = _elapsed_ms(t0)
            if resolution.status == "ambiguous":
                return _ambiguous_context_result(
                    db_path=self.db_path,
                    repo_root=repo_root,
                    profile=config.name,
                    query=task,
                    detail_level=detail_level,
                    resolution=resolution,
                    timings=timings,
                )
            if resolution.status == "unresolved":
                label = resolution.requested or target or ""
                raise ValueError(f"Target '{label}' did not match any indexed symbol.")
            target_id = resolution.target_id
            if target_id and target_id not in symbols:
                target_node = index.conn.execute(
                    "SELECT id, parent_id, type AS kind, name, path AS file_path, "
                    "language, start_line, end_line, source_hash, "
                    "parent_id AS parent_symbol_id FROM nodes "
                    "WHERE id = ? AND type = 'file'",
                    (target_id,),
                ).fetchone()
                if target_node:
                    symbols[target_id] = dict(target_node)
            fts_seed = fts_lexical_scores(index.conn, task)
            scores, evidence = lexical_scores(task, symbols, summaries, fts_seed=fts_seed)
            if target_id:
                scores[target_id] += 4.0
                evidence[target_id].append("target")
            timings["scoring"] = _elapsed_ms(t0)

            t0 = time.perf_counter()
            anchors = (
                [target_id]
                if target_id
                else [
                    nid
                    for nid, score in sorted(
                        scores.items(), key=lambda item: (-item[1], 0 if item[0].startswith("symbol::") else 1, item[0])
                    )
                    if score > 0
                ]
            )
            for anchor in anchors:
                apply_graph_expansion_from_maps(
                    anchor,
                    config.graph_radius,
                    scores,
                    evidence,
                    outgoing,
                    incoming,
                    symbols,
                )
            timings["graph_expansion"] = _elapsed_ms(t0)

            context_ids = _select_context_ids(
                target_id,
                task,
                config.name,
                scores,
                evidence,
                symbols,
                summaries,
                outgoing,
                incoming,
                config.context_budget,
            )
            dependency_budget = _dependency_budget(config)
            metrics = compute_metrics(
                task,
                target_id,
                context_ids,
                symbols,
                summaries,
                outgoing,
                dependency_budget=dependency_budget,
            )
            if detail_level in {"auto", "standard"} and not _is_sufficient(metrics, config):
                recovered_context_ids = _recover_sufficiency_context_ids(
                    target_id=target_id,
                    task=task,
                    context_ids=context_ids,
                    budget=config.context_budget,
                    metrics=metrics,
                    config=config,
                    symbols=symbols,
                    summaries=summaries,
                    evidence=evidence,
                    scores=scores,
                    outgoing=outgoing,
                    incoming=incoming,
                    index=index,
                    node_rows_light=snapshot.node_rows_light,
                )
                if recovered_context_ids != context_ids:
                    context_ids = recovered_context_ids
                    metrics = compute_metrics(
                        task,
                        target_id,
                        context_ids,
                        symbols,
                        summaries,
                        outgoing,
                        dependency_budget=dependency_budget,
                    )
            raw_nodes = raw_code_nodes(
                target_id,
                context_ids,
                outgoing,
                metrics,
                config.raw_code_budget,
                confidence_threshold=config.confidence_threshold,
            )

            t0 = time.perf_counter()
            returned_detail_level = "minimal" if detail_level == "auto" else detail_level
            nodes, metrics, sufficient = _build_detail_pass(
                detail_level=returned_detail_level,
                target_id=target_id,
                context_ids=context_ids,
                evidence=evidence,
                scores=scores,
                summaries=summaries,
                incoming=incoming,
                outgoing=outgoing,
                symbols=symbols,
                raw_nodes=raw_nodes,
                source_neighbor_budget=_source_neighbor_budget(config),
                task=task,
                config=config,
                repo_root=repo_root,
                include_source=include_source,
                explain=explain or returned_detail_level == "full",
                max_tokens=max_tokens,
                snapshot=snapshot,
                index=index,
            )

            # Auto should judge the compact response it will actually return before
            # promoting to standard; the full candidate pool is intentionally noisy.
            minimal_omitted_selected_context = (
                detail_level == "auto"
                and returned_detail_level == "minimal"
                and len(nodes) < len(context_ids)
            )
            if detail_level == "auto" and (not sufficient or minimal_omitted_selected_context):
                nodes, metrics, sufficient = _build_detail_pass(
                    detail_level="standard",
                    target_id=target_id,
                    context_ids=context_ids,
                    evidence=evidence,
                    scores=scores,
                    summaries=summaries,
                    incoming=incoming,
                    outgoing=outgoing,
                    symbols=symbols,
                    raw_nodes=raw_nodes,
                    source_neighbor_budget=_source_neighbor_budget(config),
                    task=task,
                    config=config,
                    repo_root=repo_root,
                    include_source=include_source,
                    explain=explain,
                    max_tokens=max_tokens,
                    snapshot=snapshot,
                    index=index,
                )
                returned_detail_level = "standard"
            timings["detail_pass"] = _elapsed_ms(t0)

            final_raw_nodes = {node.id for node in nodes if node.raw_code}
            relationships = _build_context_relationships(
                index,
                nodes,
                outgoing,
                incoming,
                target_id=target_id,
                returned_detail_level=returned_detail_level,
                source_policy=include_source,
                profile_name=config.name,
            )
            import_preludes = _build_import_preludes(
                index,
                nodes,
                returned_detail_level=returned_detail_level,
                source_policy=include_source,
            )
            node_tokens = sum(node.estimated_tokens for node in nodes)
            prelude_tokens = sum(
                _estimate_tokens(prelude.text) for prelude in import_preludes if prelude.text
            )
            if max_tokens is not None and node_tokens + prelude_tokens > max_tokens:
                import_preludes = []
                prelude_tokens = 0
            estimated_tokens = node_tokens + prelude_tokens

            # Aggregate confidence tiers for edges among the returned context nodes.
            confidence_counts: Dict[str, int] = {}
            for relationship in relationships:
                tier = relationship.confidence_tier or "EXTRACTED"
                confidence_counts[tier] = confidence_counts.get(tier, 0) + 1

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
            failure_reasons = _sufficiency_failure_reasons(metrics, config) if not sufficient else []

            return ContextResult(
                command="context",
                db_path=self.db_path,
                repo_root=repo_root,
                profile=config.name,
                query=task,
                target=target_id,
                target_resolution=resolution.status,
                target_candidates=list(resolution.candidates),
                detail_level=detail_level,
                returned_detail_level=returned_detail_level,
                sufficiency=SufficiencyResult(
                    sufficient=sufficient,
                    metrics=metrics,
                    thresholds={
                        "dependency_completeness": config.dep_threshold,
                        "entity_coverage": config.entity_threshold,
                        "semantic_overlap": config.semantic_threshold,
                        "semantic_overlap_relaxed": config.semantic_threshold_relaxed,
                        "model_confidence": config.confidence_threshold,
                        "dependency_budget": float(dependency_budget),
                    },
                    failure_reasons=failure_reasons,
                    recovery=_sufficiency_recovery(
                        sufficient=sufficient,
                        returned_detail_level=returned_detail_level,
                        source_policy=include_source,
                        target_id=target_id,
                        failure_reasons=failure_reasons,
                    ),
                ),
                total_estimated_tokens=estimated_tokens,
                nodes=nodes,
                relationships=relationships,
                import_preludes=import_preludes,
                target_input=target,
                source_policy=include_source,
                raw_code_nodes=sorted(final_raw_nodes),
                next_actions=_next_actions(returned_detail_level, target_id),
                warnings=_warnings(sufficient),
                run_id=run_id,
                confidence_breakdown=confidence_counts,
                timings_ms=timings,
            )
        finally:
            index.close()


def _assemble_context_nodes(
    *,
    repo_root: str,
    context_ids: Sequence[str],
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
    evidence: Dict[str, List[str]],
    scores: Dict[str, float],
    source_ids: set[str],
    target_id: str,
    outgoing: Dict[str, List[Dict[str, Any]]],
    incoming: Dict[str, List[Dict[str, Any]]],
    raw_nodes: Sequence[str],
    explain: bool,
    returned_detail_level: str,
    source_policy: str,
) -> List[ContextNode]:
    nodes: List[ContextNode] = []
    target_row = symbols.get(target_id, {})
    for node_id in context_ids:
        row = symbols[node_id]
        raw_evidence = evidence.get(node_id, [])
        lineage = sorted({e for e in raw_evidence if e.startswith("expanded-from-")})
        clean_evidence = sorted({e for e in raw_evidence if not e.startswith("expanded-from-")})
        raw_summary = summaries.get(node_id, "")
        summary = (
            _truncate_text(raw_summary, MINIMAL_SUMMARY_CHAR_LIMIT)
            if returned_detail_level == "minimal" or source_policy == "never"
            else raw_summary
        )
        source_text = _read_node_source(repo_root, row) if node_id in source_ids else None
        source_omitted_reason = (
            None
            if source_text is not None
            else _source_omitted_reason(
                row=row,
                node_id=node_id,
                source_ids=source_ids,
                returned_detail_level=returned_detail_level,
                source_policy=source_policy,
            )
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
        node_score = round(scores.get(node_id, 0.0), 4)
        reason_details = build_reason_details(
            reasons=reason,
            node_id=node_id,
            target_id=target_id,
            score=node_score,
            outgoing=outgoing,
            incoming=incoming,
        )
        nodes.append(
            ContextNode(
                id=node_id,
                kind=row["kind"],
                name=row["name"],
                path=row["file_path"],
                line_range=_line_range(row["start_line"], row["end_line"]),
                score=node_score,
                language=row["language"],
                raw_code=node_id in raw_nodes and source_text is not None,
                evidence=clean_evidence,
                summary=summary,
                lineage=lineage,
                source_text=source_text,
                estimated_tokens=estimated_tokens,
                reason=reason,
                reason_details=reason_details,
                explanation=build_explanation(reason) if explain else None,
                source_omitted_reason=source_omitted_reason,
            )
        )
    return nodes


def _source_omitted_reason(
    *,
    row: Dict[str, Any],
    node_id: str,
    source_ids: set[str],
    returned_detail_level: str,
    source_policy: str,
) -> str:
    if returned_detail_level == "minimal":
        return "minimal_detail"
    if source_policy == "never":
        return "source_policy_never"
    if row.get("kind") == "file":
        return "file_node_metadata_only"
    if row.get("start_line") is None or row.get("end_line") is None:
        return "no_source_span"
    if source_policy == "auto" and node_id not in source_ids:
        return "auto_source_budget"
    return "source_unavailable"


def _build_context_relationships(
    index: ProjectIndex,
    nodes: Sequence[ContextNode],
    outgoing: Dict[str, List[Dict[str, Any]]],
    incoming: Dict[str, List[Dict[str, Any]]],
    *,
    target_id: str,
    returned_detail_level: str,
    source_policy: str,
    profile_name: str,
) -> List[ContextRelationship]:
    selected_ids = {node.id for node in nodes}
    selected_order = [node.id for node in nodes]
    rank_by_id = {node.id: rank for rank, node in enumerate(nodes)}
    path_by_id = {node.id: node.path for node in nodes}
    file_ids = {file_node_id(node.path): node.path for node in nodes if node.path}
    relationships: List[ContextRelationship] = []
    seen: set[tuple[str, str, str, str]] = set()
    include_snippets = _include_occurrence_snippets(returned_detail_level, source_policy)

    def add(edge: Dict[str, Any], source_path: Optional[str], target_path: Optional[str]) -> None:
        relation = str(edge.get("relation") or "")
        if relation == "contains" or not relation:
            return
        metadata_raw = edge.get("metadata")
        metadata = json_loads(metadata_raw) if isinstance(metadata_raw, str) else {}
        occurrences: List[RelationshipOccurrence] = []
        if relation == "imports":
            occurrences = _import_occurrences_from_metadata(
                metadata,
                path=source_path,
                include_snippet=include_snippets,
            )
            metadata = dict(metadata)
            metadata.pop("source", None)
        key = (
            str(edge.get("source_id") or edge.get("source")),
            str(edge.get("target_id") or edge.get("target")),
            relation,
            str(metadata_raw or ""),
        )
        if key in seen:
            return
        seen.add(key)
        relationships.append(
            ContextRelationship(
                source=key[0],
                target=key[1],
                relation=relation,
                metadata=metadata,
                confidence=float(edge.get("confidence") or 1.0),
                confidence_tier=str(edge.get("confidence_tier") or "EXTRACTED"),
                source_path=source_path,
                target_path=target_path,
                occurrences=occurrences,
            )
        )

    for node_id in selected_order:
        for edge in outgoing.get(node_id, []):
            target = str(edge.get("target_id") or edge.get("target"))
            if target in selected_ids:
                add(edge, path_by_id.get(node_id), path_by_id.get(target))
        for edge in incoming.get(node_id, []):
            source = str(edge.get("source_id") or edge.get("source"))
            if source in selected_ids:
                add(edge, path_by_id.get(source), path_by_id.get(node_id))

    for source_file_id, source_path in file_ids.items():
        for edge in outgoing.get(source_file_id, []):
            target = str(edge.get("target_id") or edge.get("target"))
            if edge.get("relation") == "imports" and target in file_ids:
                add(edge, source_path, file_ids.get(target))
    relationships.sort(key=lambda relationship: _relationship_priority(relationship, target_id, rank_by_id))
    occurrence_limit = OCCURRENCE_LIMIT_BY_PROFILE.get(profile_name, 24)
    _attach_symbol_reference_occurrences(
        index,
        relationships,
        include_snippet=include_snippets,
        total_limit=occurrence_limit,
    )
    _trim_relationship_occurrences(relationships, total_limit=occurrence_limit)
    return relationships


def _relationship_priority(
    relationship: ContextRelationship,
    target_id: str,
    rank_by_id: Dict[str, int],
) -> tuple[int, int, int, int, str, str]:
    relation_rank = {
        "calls": 0,
        "called_by": 1,
        "inherits": 2,
        "decorates": 3,
        "tested_by": 4,
        "imports": 5,
    }.get(relationship.relation, 9)
    source_rank = rank_by_id.get(relationship.source, len(rank_by_id) + 1000)
    target_rank = rank_by_id.get(relationship.target, len(rank_by_id) + 1000)
    if relationship.source == target_id and relationship.relation == "calls":
        adjacency_rank = 0
    elif relationship.target == target_id and relationship.relation == "calls":
        adjacency_rank = 1
    elif relationship.source == target_id or relationship.target == target_id:
        adjacency_rank = 2
    else:
        adjacency_rank = 3
    return (
        adjacency_rank,
        min(source_rank, target_rank),
        max(source_rank, target_rank),
        relation_rank,
        relationship.source,
        relationship.target,
    )


def _include_occurrence_snippets(returned_detail_level: str, source_policy: str) -> bool:
    return returned_detail_level in {"standard", "full"} and source_policy != "never"


def _import_occurrences_from_metadata(
    metadata: Dict[str, Any],
    *,
    path: Optional[str],
    include_snippet: bool,
) -> List[RelationshipOccurrence]:
    source = str(metadata.get("source") or "")
    start_line = metadata.get("start_line")
    end_line = metadata.get("end_line")
    if not path or start_line is None or end_line is None:
        return []
    line_range = _line_range(int(start_line), int(end_line))
    return [
        RelationshipOccurrence(
            path=path,
            line_range=line_range,
            enclosing_symbol_id=None,
            name=str(metadata.get("import") or ""),
            kind="imports",
            snippet=_truncate_occurrence_snippet(source) if include_snippet and source else None,
            metadata={
                key: value
                for key, value in metadata.items()
                if key not in {"source", "start_line", "end_line"}
            },
        )
    ]


def _attach_symbol_reference_occurrences(
    index: ProjectIndex,
    relationships: Sequence[ContextRelationship],
    *,
    include_snippet: bool,
    total_limit: int,
) -> None:
    used = 0
    repo_root = str(index.metadata()["root_dir"]) if include_snippet else ""
    for relationship in relationships:
        if used >= total_limit:
            return
        if relationship.relation == "imports" or relationship.occurrences:
            continue
        rows = _relationship_reference_rows(index, relationship)
        for row in rows[:OCCURRENCE_PER_RELATIONSHIP_LIMIT]:
            if used >= total_limit:
                return
            path = str(row["path"] or "")
            start_line = row["start_line"]
            end_line = row["end_line"]
            snippet = str(row["source"] or "")
            if include_snippet and not snippet and path and start_line is not None and end_line is not None:
                snippet = (
                    read_source_lines(
                        repo_root,
                        path,
                        int(start_line),
                        int(end_line),
                    )
                    or ""
                )
            metadata_raw = row["metadata"]
            metadata = json_loads(metadata_raw) if isinstance(metadata_raw, str) else {}
            relationship.occurrences.append(
                RelationshipOccurrence(
                    path=path,
                    line_range=_line_range(start_line, end_line),
                    enclosing_symbol_id=str(row["enclosing_symbol_id"] or "")
                    or None,
                    name=str(row["name"] or ""),
                    kind=str(row["kind"] or relationship.relation),
                    snippet=_truncate_occurrence_snippet(snippet)
                    if include_snippet and snippet
                    else None,
                    metadata=metadata,
                )
            )
            used += 1


def _trim_relationship_occurrences(
    relationships: Sequence[ContextRelationship],
    *,
    total_limit: int,
) -> None:
    used = 0
    for relationship in relationships:
        if not relationship.occurrences:
            continue
        remaining = total_limit - used
        if remaining <= 0:
            relationship.occurrences = []
            continue
        if len(relationship.occurrences) > remaining:
            relationship.occurrences = relationship.occurrences[:remaining]
            used = total_limit
            continue
        used += len(relationship.occurrences)


def _relationship_reference_rows(
    index: ProjectIndex,
    relationship: ContextRelationship,
) -> List[Any]:
    if relationship.relation == "tested_by":
        enclosing_symbol_id = relationship.target
        target = relationship.source
    elif relationship.relation == "decorates":
        enclosing_symbol_id = relationship.target
        target = relationship.source
    else:
        enclosing_symbol_id = relationship.source
        target = relationship.target
    return list(
        index.conn.execute(
            """
            SELECT
                COALESCE(n.path, REPLACE(sr.source_file_id, 'file::', '')) AS path,
                sr.enclosing_symbol_id,
                sr.target,
                sr.kind,
                sr.name,
                sr.start_line,
                sr.end_line,
                sr.source,
                sr.metadata
            FROM symbol_references sr
            LEFT JOIN nodes n ON n.id = sr.source_file_id
            WHERE sr.enclosing_symbol_id = ?
              AND sr.target = ?
              AND sr.kind = ?
            ORDER BY sr.start_line, sr.end_line, sr.name
            """,
            (enclosing_symbol_id, target, relationship.relation),
        ).fetchall()
    )


def _truncate_occurrence_snippet(source: str) -> str:
    lines = source.splitlines()[:3]
    snippet = "\n".join(lines)
    if len(snippet) > 240:
        return snippet[:237].rstrip() + "..."
    return snippet


def _build_import_preludes(
    index: ProjectIndex,
    nodes: Sequence[ContextNode],
    *,
    returned_detail_level: str,
    source_policy: str,
) -> List[ImportPrelude]:
    if returned_detail_level == "minimal":
        return []
    by_path: Dict[str, List[ContextNode]] = {}
    for node in nodes:
        if node.kind != "file" and node.path:
            by_path.setdefault(node.path, []).append(node)
    if not by_path:
        return []

    selected_file_ids = {file_node_id(path) for path in by_path}
    selected_symbol_names_by_path = {
        path: {node.name for node in path_nodes if node.name}
        for path, path_nodes in by_path.items()
    }
    include_text = source_policy in {"auto", "always", "never"}
    total_lines = 0
    total_chars = 0
    preludes: List[ImportPrelude] = []
    for path in sorted(by_path)[:IMPORT_PRELUDE_FILE_LIMIT]:
        file_id = file_node_id(path)
        rows = index.conn.execute(
            """
            SELECT
                path, language, import_name, resolved_file_id, start_line,
                end_line, source, metadata
            FROM imports
            WHERE file_id = ?
            ORDER BY start_line, end_line, import_name
            """,
            (file_id,),
        ).fetchall()
        if not rows:
            continue
        snippets: List[str] = []
        resolved: List[str] = []
        start_line: Optional[int] = None
        end_line: Optional[int] = None
        for row in rows[:12]:
            source = str(row["source"] or "")
            resolved_file_id = str(row["resolved_file_id"] or "")
            if not _import_prelude_row_is_relevant(
                row,
                by_path[path],
                selected_file_ids,
                selected_symbol_names_by_path,
            ):
                continue
            if include_text:
                if not source:
                    continue
                line_count = max(1, source.count("\n") + 1)
                if total_lines + line_count > IMPORT_PRELUDE_TOTAL_LINE_LIMIT:
                    break
                if total_chars + len(source) > IMPORT_PRELUDE_TOTAL_CHAR_LIMIT:
                    break
                snippets.append(source)
                total_lines += line_count
                total_chars += len(source)
            row_start = int(row["start_line"] or 1)
            row_end = int(row["end_line"] or row_start)
            start_line = row_start if start_line is None else min(start_line, row_start)
            end_line = row_end if end_line is None else max(end_line, row_end)
            if resolved_file_id:
                resolved.append(resolved_file_id)
        if start_line is not None and end_line is not None:
            preludes.append(
                ImportPrelude(
                    path=path,
                    language=str(rows[0]["language"] or ""),
                    text="\n".join(dict.fromkeys(snippets)) if include_text else "",
                    line_range=[start_line or 1, end_line or 1],
                    source_node_ids=[node.id for node in by_path[path]],
                    resolved_imports=sorted(set(resolved)),
                )
            )
    return preludes


def _import_prelude_row_is_relevant(
    row: Any,
    nodes: Sequence[ContextNode],
    selected_file_ids: set[str],
    selected_symbol_names_by_path: Dict[str, set[str]],
) -> bool:
    resolved_file_id = str(row["resolved_file_id"] or "")
    source = str(row["source"] or "")
    import_name = str(row["import_name"] or "")
    imported_names = _imported_symbol_names(source, import_name)
    if resolved_file_id and resolved_file_id in selected_file_ids:
        selected_names = selected_symbol_names_by_path.get(
            resolved_file_id.removeprefix("file::"),
            set(),
        )
        return not imported_names or not selected_names or bool(imported_names & selected_names)
    metadata_raw = row["metadata"]
    metadata = json_loads(metadata_raw) if isinstance(metadata_raw, str) else {}
    candidate_names = {
        import_name.rsplit(".", 1)[-1].rsplit("/", 1)[-1],
        str(metadata.get("imported_name") or ""),
        str(metadata.get("local_name") or ""),
    }
    candidate_names = {name for name in candidate_names if name}
    if not candidate_names:
        return False
    selected_text = " ".join(
        value
        for node in nodes
        for value in (node.name, node.summary, node.source_text or "")
        if value
    )
    selected_tokens = _task_tokens(selected_text)
    return bool({name.lower() for name in candidate_names} & selected_tokens)


def _ambiguous_context_result(
    *,
    db_path: str,
    repo_root: str,
    profile: str,
    query: str,
    detail_level: str,
    resolution: TargetResolution,
    timings: Dict[str, float],
) -> ContextResult:
    label = resolution.requested or ""
    candidates = resolution.candidates
    return ContextResult(
        command="context",
        db_path=db_path,
        repo_root=repo_root,
        profile=profile,
        query=query,
        target=label,
        target_resolution="ambiguous",
        target_candidates=list(candidates),
        detail_level=detail_level,
        returned_detail_level="minimal",
        sufficiency=SufficiencyResult(
            sufficient=False,
            metrics=SufficiencyMetrics(
                dependency_completeness=0.0,
                entity_coverage=0.0,
                semantic_overlap=0.0,
                model_confidence=0.0,
            ),
            thresholds={},
        ),
        total_estimated_tokens=0,
        nodes=[],
        relationships=[],
        import_preludes=[],
        target_input=resolution.requested,
        raw_code_nodes=[],
        next_actions=[
            {
                "action": "resolve_target",
                "reason": "Multiple symbols matched; pass node id or qualified name before editing.",
                "candidates": candidates,
            }
        ],
        warnings=[
            f"Target '{label}' matched {len(candidates)} indexed symbols. "
            "Re-run csegraph_context with a specific target from target.candidates."
        ],
        confidence_breakdown={},
        timings_ms=timings,
    )


def _select_context_ids(
    target_id: str,
    task: str,
    profile_name: str,
    scores: Dict[str, float],
    evidence: Dict[str, List[str]],
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
    outgoing: Dict[str, List[Dict[str, Any]]],
    incoming: Dict[str, List[Dict[str, Any]]],
    budget: int,
) -> List[str]:
    baseline_score = 0.01
    adaptive_budget = min(budget, max(MINIMAL_NODE_LIMIT, budget))
    caps = _relation_caps(profile_name)

    # Deterministic sort for context_ids (descending node_id as tie-breaker)
    sorted_nodes = sorted(
        scores.keys(),
        key=lambda n: (-scores.get(n, 0.0), 0 if n.startswith("symbol::") else 1, n)
    )
    context_ids = [n for n in sorted_nodes if scores.get(n, 0.0) > baseline_score]
    
    task_tokens = _task_tokens(task)
    groups = _neighborhood_groups(
        target_id,
        task_tokens,
        scores,
        evidence,
        symbols,
        summaries,
        outgoing,
        incoming,
        caps,
    )
    selected: List[str] = []
    seen: set[str] = set()
    selected_by_path: Dict[str, int] = {}

    def append_group(group: Sequence[str]) -> None:
        for node_id in group:
            if node_id not in symbols or node_id in seen or len(selected) >= adaptive_budget:
                continue
            path = str(symbols.get(node_id, {}).get("file_path") or "")
            if (
                node_id != target_id
                and "bounded-callee" in evidence.get(node_id, [])
                and path
                and selected_by_path.get(path, 0) >= DIRECT_CALLEE_PATH_LIMIT
                and len(selected) >= MINIMAL_NODE_LIMIT
            ):
                continue
            if (
                node_id != target_id
                and "bounded-callee" not in evidence.get(node_id, [])
                and path
                and scores.get(node_id, 0.0) < DIRECT_CALL_SCORE_FLOOR
                and selected_by_path.get(path, 0) >= 2
                and len(selected) >= MINIMAL_NODE_LIMIT
            ):
                continue
            selected.append(node_id)
            seen.add(node_id)
            if path:
                selected_by_path[path] = selected_by_path.get(path, 0) + 1

    for group in groups:
        append_group(group)

    remaining = adaptive_budget - len(selected)
    if remaining > 0:
        top_score = max(scores.values(), default=0.0)
        ranked_floor = max(RANKED_SCORE_FLOOR, top_score * RANKED_SCORE_RATIO_FLOOR)
        for node_id in _rank_nodes(
            scores.keys(),
            task_tokens,
            scores,
            evidence,
            symbols,
            summaries,
        ):
            if node_id not in seen:
                if (
                    node_id != target_id
                    and not _is_test_query_tokens(task_tokens)
                    and _is_test_symbol_row(symbols[node_id])
                ):
                    continue
                if scores.get(node_id, 0.0) <= baseline_score:
                    continue
                if scores.get(node_id, 0.0) < ranked_floor and len(selected) >= MINIMAL_NODE_LIMIT:
                    continue
                selected.append(node_id)
                seen.add(node_id)
                if len(selected) >= adaptive_budget:
                    break
    return selected


def _relation_caps(profile_name: str) -> Dict[str, int]:
    multiplier = {"small": 1, "medium": 2, "large": 3}.get(profile_name, 1)
    return {
        "target_file_symbols": 8 * multiplier,
        "callees": 24 * multiplier,
        "callers": 4 * multiplier,
        "same_file": 4 * multiplier,
        "imported": 6 * multiplier,
    }


def _neighborhood_groups(
    target_id: str,
    task_tokens: set[str],
    scores: Dict[str, float],
    evidence: Dict[str, List[str]],
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
    outgoing: Dict[str, List[Dict[str, Any]]],
    incoming: Dict[str, List[Dict[str, Any]]],
    caps: Dict[str, int],
) -> List[List[str]]:
    target_row = symbols.get(target_id, {})
    groups: List[List[str]] = []
    if target_id:
        if target_row.get("kind") == "file":
            groups.append(
                _rank_nodes(
                    [
                        node_id
                        for node_id, row in symbols.items()
                        if row.get("file_path") == target_row.get("file_path")
                        and row.get("kind") != "file"
                    ],
                    task_tokens,
                    scores,
                    evidence,
                    symbols,
                    summaries,
                )[: caps["target_file_symbols"]]
            )
        else:
            groups.append([target_id])

    direct_callees = [
        edge["target"]
        for edge in outgoing.get(target_id, [])
        if edge.get("relation") == "calls" and edge.get("target") in symbols
    ]
    for node_id in direct_callees:
        evidence[node_id].append("bounded-callee")

    imported = _import_symbol_candidates(target_id, symbols, outgoing)
    imported = [
        node_id
        for node_id in imported
        if _is_relevant_neighbor(node_id, task_tokens, scores, evidence, symbols, summaries)
    ]
    direct_imported_callees = [
        node_id for node_id in imported if node_id in set(direct_callees)
    ]
    for node_id in direct_imported_callees:
        evidence[node_id].append("bounded-imported-callee")
    groups.append(
        _rank_nodes(
            direct_imported_callees,
            task_tokens,
            scores,
            evidence,
            symbols,
            summaries,
        )[: caps["imported"]]
    )

    groups.append(
        _rank_nodes(direct_callees, task_tokens, scores, evidence, symbols, summaries)[
            : caps["callees"]
        ]
    )

    callers = [
        edge["source"]
        for edge in incoming.get(target_id, [])
        if edge.get("relation") == "calls" and edge.get("source") in symbols
    ]
    callers = [
        node_id
        for node_id in callers
        if _is_test_query_tokens(task_tokens) or not _is_test_symbol_row(symbols[node_id])
    ]
    for node_id in callers:
        evidence[node_id].append("bounded-caller")
    groups.append(
        _rank_nodes(callers, task_tokens, scores, evidence, symbols, summaries)[: caps["callers"]]
    )

    target_path = target_row.get("file_path")
    if target_path:
        same_file = [
            node_id
            for node_id, row in symbols.items()
            if node_id != target_id
            and row.get("file_path") == target_path
            and row.get("kind") != "file"
            and _is_relevant_neighbor(node_id, task_tokens, scores, evidence, symbols, summaries)
        ]
        for node_id in same_file:
            evidence[node_id].append("bounded-same-file")
        groups.append(
            _rank_nodes(same_file, task_tokens, scores, evidence, symbols, summaries)[
                : caps["same_file"]
            ]
        )

    for node_id in imported:
        evidence[node_id].append("bounded-import")
    groups.append(
        _rank_nodes(imported, task_tokens, scores, evidence, symbols, summaries)[
            : caps["imported"]
        ]
    )
    return groups


def _rank_nodes(
    node_ids: Iterable[str],
    task_tokens: set[str],
    scores: Dict[str, float],
    evidence: Dict[str, List[str]],
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
) -> List[str]:
    unique = list(
        dict.fromkeys(
            node_id
            for node_id in node_ids
            if node_id in symbols and symbols[node_id].get("kind") != "file"
        )
    )
    return sorted(
        unique,
        key=lambda node_id: (
            -_relation_rank_score(node_id, task_tokens, scores, evidence, symbols, summaries),
            0 if node_id.startswith("symbol::") else 1,
            node_id,
        )
    )


def _relation_rank_score(
    node_id: str,
    task_tokens: set[str],
    scores: Dict[str, float],
    evidence: Dict[str, List[str]],
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
) -> float:
    row = symbols.get(node_id, {})
    text = " ".join(
        str(value or "")
        for value in (
            row.get("name"),
            row.get("file_path"),
            row.get("signature"),
            row.get("docstring"),
            summaries.get(node_id, ""),
        )
    )
    overlap = task_tokens & _task_tokens(text)
    lexical_bonus = 1.5 if _has_lexical_evidence(evidence.get(node_id, [])) else 0.0
    class_bonus = 0.4 if row.get("kind") == "class" else 0.0
    return scores.get(node_id, 0.0) + (2.0 * len(overlap)) + lexical_bonus + class_bonus


def _is_relevant_neighbor(
    node_id: str,
    task_tokens: set[str],
    scores: Dict[str, float],
    evidence: Dict[str, List[str]],
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
) -> bool:
    if scores.get(node_id, 0.0) >= DIRECT_CALL_SCORE_FLOOR:
        return True
    if _has_lexical_evidence(evidence.get(node_id, [])):
        return True
    return bool(task_tokens & _task_tokens(_node_search_text(node_id, symbols, summaries)))


def _import_symbol_candidates(
    target_id: str,
    symbols: Dict[str, Dict[str, Any]],
    outgoing: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    target_row = symbols.get(target_id, {})
    target_path = target_row.get("file_path")
    if not target_path:
        return []
    imported_names_by_path: Dict[str, set[str]] = {}
    import_edges = [
        edge
        for edge in outgoing.get(file_node_id(str(target_path)), [])
        if edge.get("relation") == "imports"
    ]
    for edge in import_edges:
        target_file_id = str(edge.get("target_id") or edge.get("target") or "")
        if not target_file_id:
            continue
        path = target_file_id.removeprefix("file::")
        imported_names_by_path.setdefault(path, set()).update(
            _imported_symbol_names_from_edge(edge)
        )
    imported_paths = set(imported_names_by_path)
    return [
        node_id
        for node_id, row in symbols.items()
        if row.get("file_path") in imported_paths
        and row.get("kind") != "file"
        and (
            not imported_names_by_path[str(row.get("file_path"))]
            or str(row.get("name") or "") in imported_names_by_path[str(row.get("file_path"))]
        )
    ]


def _imported_symbol_names_from_edge(edge: Dict[str, Any]) -> set[str]:
    metadata_raw = edge.get("metadata")
    metadata = json_loads(metadata_raw) if isinstance(metadata_raw, str) else {}
    return _imported_symbol_names(
        str(metadata.get("source") or "").strip(),
        str(metadata.get("import") or "").strip(),
    )


def _imported_symbol_names(source: str, import_name: str) -> set[str]:
    names: set[str] = set()
    named_import = re.search(r"\bimport\s*\{([^}]+)\}", source, re.DOTALL)
    if source.startswith("from "):
        match = re.search(r"\bimport\s+(.+)", source, re.DOTALL)
        if match:
            names.update(_split_import_names(match.group(1)))
    elif named_import:
        names.update(_split_import_names(named_import.group(1)))
    if not names and "." in import_name:
        names.add(import_name.rsplit(".", 1)[-1])
    return names


def _split_import_names(raw: str) -> set[str]:
    cleaned = raw.replace("(", "").replace(")", "")
    cleaned = cleaned.split("#", 1)[0]
    names: set[str] = set()
    for part in cleaned.split(","):
        token = part.strip()
        if not token:
            continue
        if token == "*":
            return set()
        token = token.split(" as ", 1)[0].strip()
        token = token.split(":", 1)[0].strip()
        token = token.rsplit(".", 1)[-1]
        if token and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", token):
            names.add(token)
    return names


def _node_search_text(
    node_id: str,
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
) -> str:
    row = symbols.get(node_id, {})
    return " ".join(
        str(value or "")
        for value in (
            row.get("name"),
            row.get("file_path"),
            row.get("signature"),
            row.get("docstring"),
            summaries.get(node_id, ""),
        )
    )


def _task_tokens(text: str) -> set[str]:
    pieces = re.findall(r"[A-Za-z0-9]+", text)
    tokens: set[str] = set()
    for piece in pieces:
        tokens.add(piece.lower())
        tokens.update(part.lower() for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)", piece))
    return {token for token in tokens if len(token) > 1}


def _is_test_query_tokens(task_tokens: set[str]) -> bool:
    return bool(task_tokens & {"test", "tests", "pytest", "coverage", "failing", "failure"})


def _is_test_symbol_row(row: Dict[str, Any]) -> bool:
    kind = str(row.get("kind") or row.get("type") or "")
    name = str(row.get("name") or "").lower()
    path = str(row.get("file_path") or row.get("path") or "").lower()
    return (
        kind == "test"
        or name.startswith("test_")
        or path.startswith("tests/")
        or "/tests/" in path
        or "/__tests__/" in path
    )


def _has_lexical_evidence(items: Sequence[str]) -> bool:
    return any(
        item in {"fts5-bm25", "lexical-token-overlap", "exact-symbol-name", "file-path-match"}
        for item in items
    )


def _validate_include_source(value: str) -> str:
    valid = {"auto", "always", "never"}
    if value not in valid:
        choices = ", ".join(sorted(valid))
        raise ValueError(f"include_source must be one of: {choices}")
    return value


def _validate_detail_level(value: str) -> str:
    if value not in DETAIL_LEVELS:
        choices = ", ".join(sorted(DETAIL_LEVELS))
        raise ValueError(f"detail_level must be one of: {choices}")
    return value


def _returned_detail_level(detail_level: str, sufficient: bool) -> str:
    if detail_level == "auto":
        return "minimal" if sufficient else "standard"
    return detail_level


def _response_context_ids(
    returned_detail_level: str,
    target_id: str,
    context_ids: Sequence[str],
) -> List[str]:
    if returned_detail_level != "minimal":
        return list(context_ids)
    selected: List[str] = []
    seen: set[str] = set()
    for node_id in itertools.chain((target_id,), context_ids):
        if node_id not in seen:
            selected.append(node_id)
            seen.add(node_id)
        if len(selected) >= MINIMAL_NODE_LIMIT:
            break
    return selected


def _source_candidate_ids_for_detail(
    returned_detail_level: str,
    include_source: str,
    target_id: str,
    context_ids: Sequence[str],
    outgoing: Dict[str, List[Dict[str, Any]]],
    symbols: Dict[str, Dict[str, Any]],
    raw_nodes: Sequence[str],
    source_neighbor_budget: int,
) -> set[str]:
    if returned_detail_level == "minimal":
        return set()
    if returned_detail_level == "full":
        return set(context_ids)
    return _source_candidate_ids(
        include_source,
        target_id,
        context_ids,
        outgoing,
        symbols,
        raw_nodes,
        source_neighbor_budget,
    )


def _source_candidate_ids(
    include_source: str,
    target_id: str,
    context_ids: Sequence[str],
    outgoing: Dict[str, List[Dict[str, Any]]],
    symbols: Dict[str, Dict[str, Any]],
    raw_nodes: Sequence[str],
    source_neighbor_budget: int,
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
    direct_source_order = [
        node_id
        for node_id in context_ids
        if node_id in direct_calls
    ]
    direct_sources = set(direct_source_order[: max(0, source_neighbor_budget)])
    small_helpers = {
        node_id for node_id in context_set if is_small_helper_row(symbols.get(node_id, {}))
    }
    remaining_helper_budget = max(0, source_neighbor_budget - len(direct_sources))
    helper_sources = {
        node_id
        for node_id in context_ids
        if node_id in small_helpers and node_id not in direct_sources
    }
    helper_sources = set(list(helper_sources)[:remaining_helper_budget])
    return ({target_id} | set(raw_nodes) | direct_sources | helper_sources) & context_set


def _next_actions(
    returned_detail_level: str,
    target_id: str,
) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    if returned_detail_level == "minimal":
        actions.append(
            {
                "action": "expand_context",
                "detail_level": "standard",
                "reason": "Request working context with selected source before editing.",
            }
        )
    if target_id:
        actions.append(
            {
                "action": "inspect_graph",
                "tool": "csegraph_graph",
                "node": target_id,
                "reason": "Inspect graph neighbors when blast radius or dependencies matter.",
            }
        )
    return actions


def _warnings(sufficient: bool) -> List[str]:
    if sufficient:
        return []
    return ["Context sufficiency thresholds were not met."]


def _is_sufficient(metrics: Any, config: Any) -> bool:
    return all_pass(
        metrics,
        dep_threshold=config.dep_threshold,
        entity_threshold=config.entity_threshold,
        semantic_threshold=config.semantic_threshold,
        semantic_threshold_relaxed=config.semantic_threshold_relaxed,
        confidence_threshold=config.confidence_threshold,
    )


def _recover_sufficiency_context_ids(
    *,
    target_id: str,
    task: str,
    context_ids: Sequence[str],
    budget: int,
    metrics: SufficiencyMetrics,
    config: Any,
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
    evidence: Dict[str, List[str]],
    scores: Dict[str, float],
    outgoing: Dict[str, List[Dict[str, Any]]],
    incoming: Dict[str, List[Dict[str, Any]]],
    raw_nodes: Sequence[str] = (),
    index: Any = None,
    node_rows_light: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[str]:
    if metrics.dependency_completeness >= config.dep_threshold and (
        metrics.entity_coverage >= config.entity_threshold
    ):
        return list(context_ids)
    task_tokens = _task_tokens(task)
    recovered: List[str] = []

    for edge in outgoing.get(target_id, []):
        if edge.get("relation") == "calls" and edge.get("target") in symbols:
            node_id = str(edge["target"])
            evidence[node_id].append("bounded-callee")
            recovered.append(node_id)
    for edge in incoming.get(target_id, []):
        if edge.get("relation") != "calls" or edge.get("source") not in symbols:
            continue
        node_id = str(edge["source"])
        if _is_test_query_tokens(task_tokens) or not _is_test_symbol_row(symbols[node_id]):
            evidence[node_id].append("bounded-caller")
            recovered.append(node_id)

    if metrics.entity_coverage < config.entity_threshold:
        if node_rows_light is not None:
            names = [str(row.get("name") or "") for row in node_rows_light.values() if row.get("type") != "file"]
        else:
            names = [str(row.get("name") or "") for row in symbols.values()]
        entities = extract_query_entities(task, names)
        for entity in entities:
            for node_id, row in symbols.items():
                if row.get("kind") == "file" or row.get("type") == "file":
                    continue
                name = str(row.get("name") or "")
                if _context_name_covers_entity(name, entity):
                    evidence[node_id].append("lexical-token-overlap")
                    recovered.append(node_id)

    merged: List[str] = []
    seen: set[str] = set()
    for node_id in itertools.chain((target_id,), recovered, context_ids):
        if not node_id or node_id in seen:
            continue
        if node_id not in symbols:
            if node_rows_light is not None and node_id in node_rows_light:
                symbols[node_id] = copy.deepcopy(node_rows_light[node_id])
            else:
                continue
        if (
            node_id != target_id
            and not _is_test_query_tokens(task_tokens)
            and _is_test_symbol_row(symbols[node_id])
        ):
            continue
        merged.append(node_id)
        seen.add(node_id)
        if len(merged) >= budget:
            break
    return merged


def _context_name_covers_entity(context_name: str, entity: str) -> bool:
    context_lower = context_name.lower()
    entity_lower = entity.lower()
    return entity_lower == context_lower or f"{entity_lower}." in context_lower


def _sufficiency_failure_reasons(metrics: SufficiencyMetrics, config: Any) -> List[Dict[str, Any]]:
    reasons: List[Dict[str, Any]] = []
    structural_ok = (
        metrics.dependency_completeness >= config.dep_threshold
        and metrics.entity_coverage >= config.entity_threshold
    )
    semantic_threshold = (
        config.semantic_threshold_relaxed
        if structural_ok
        else config.semantic_threshold
    )
    checks = (
        (
            "dependency_completeness",
            metrics.dependency_completeness,
            config.dep_threshold,
            "Retry with profile=medium or inspect csegraph_graph for dependency topology.",
        ),
        (
            "entity_coverage",
            metrics.entity_coverage,
            config.entity_threshold,
            "Use a more specific target or include the missing entity name in the task.",
        ),
        (
            "semantic_overlap",
            metrics.semantic_overlap,
            semantic_threshold,
            "Retry with detail_level=standard or refine the task wording.",
        ),
        (
            "model_confidence",
            metrics.model_confidence,
            config.confidence_threshold,
            "Escalate to profile=medium before editing.",
        ),
    )
    for metric, actual, threshold, action in checks:
        if actual < threshold:
            reasons.append(
                {
                    "metric": metric,
                    "actual": actual,
                    "threshold": threshold,
                    "suggested_next_action": action,
                }
            )
    return reasons


def _sufficiency_recovery(
    *,
    sufficient: bool,
    returned_detail_level: str,
    source_policy: str,
    target_id: str,
    failure_reasons: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if sufficient:
        return []
    recovery: List[Dict[str, Any]] = []
    failure_metrics = {str(reason.get("metric") or "") for reason in failure_reasons}
    if returned_detail_level == "minimal":
        recovery.append(
            {
                "action": "expand_context",
                "tool": "csegraph_context",
                "detail_level": "standard",
                "reason": "Minimal detail was insufficient for this task.",
            }
        )
    if source_policy == "never":
        recovery.append(
            {
                "action": "allow_snippets",
                "tool": "csegraph_context",
                "include_source": "auto",
                "reason": "Source snippets are currently suppressed.",
            }
        )
    if "dependency_completeness" in failure_metrics and target_id:
        recovery.append(
            {
                "action": "inspect_graph",
                "tool": "csegraph_graph",
                "node": target_id,
                "reason": "Dependency coverage is below threshold.",
            }
        )
    if {"entity_coverage", "semantic_overlap"} & failure_metrics:
        recovery.append(
            {
                "action": "refine_task",
                "tool": "csegraph_context",
                "reason": "Re-run with a more specific task or target phrase.",
            }
        )
    return recovery


def _dependency_budget(config: Any) -> int:
    return max(1, int(getattr(config, "context_budget", MINIMAL_NODE_LIMIT)) // 2)


def _source_neighbor_budget(config: Any) -> int:
    return max(2, int(getattr(config, "raw_code_budget", 3)) + 2)


def _build_detail_pass(
    *,
    detail_level: str,
    context_ids: Sequence[str],
    target_id: str,
    include_source: str,
    explain: bool,
    max_tokens: Optional[int],
    source_neighbor_budget: int,
    task: str,
    config: Any,
    repo_root: str,
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
    evidence: Dict[str, List[str]],
    scores: Dict[str, float],
    outgoing: Dict[str, List[Dict[str, Any]]],
    incoming: Dict[str, List[Dict[str, Any]]],
    raw_nodes: Sequence[str],
    index: ProjectIndex,
    snapshot: Any = None,
) -> tuple[List[ContextNode], SufficiencyMetrics, bool]:
    response_ids = _response_context_ids(detail_level, target_id, context_ids)
    needed_ids = set(response_ids)
    if target_id:
        needed_ids.add(target_id)
    heavy_symbols = load_symbols(index, ids=needed_ids, exclude_heavy=False)
    for node_id, heavy_row in heavy_symbols.items():
        if node_id in symbols:
            symbols[node_id].update(heavy_row)

    source_ids = _source_candidate_ids_for_detail(
        detail_level,
        include_source,
        target_id,
        response_ids,
        outgoing,
        symbols,
        raw_nodes,
        source_neighbor_budget,
    )
    nodes = _assemble_context_nodes(
        repo_root=repo_root,
        context_ids=response_ids,
        symbols=symbols,
        summaries=summaries,
        evidence=evidence,
        scores=scores,
        source_ids=source_ids,
        target_id=target_id,
        outgoing=outgoing,
        incoming=incoming,
        raw_nodes=raw_nodes,
        explain=explain,
        returned_detail_level=detail_level,
        source_policy=include_source,
    )
    nodes = _apply_token_budget(nodes, max_tokens)
    retained_ids = [node.id for node in nodes]
    metrics = compute_metrics(
        task,
        target_id,
        retained_ids,
        symbols,
        summaries,
        outgoing,
        dependency_budget=_dependency_budget(config),
    )
    sufficient = _is_sufficient(metrics, config)
    return nodes, metrics, sufficient


def _line_range(start_line: Optional[int], end_line: Optional[int]) -> Optional[List[int]]:
    if start_line is None or end_line is None:
        return None
    return [int(start_line), int(end_line)]


def _read_node_source(repo_root: str, row: Dict[str, Any]) -> Optional[str]:
    file_path = row.get("file_path")
    kind = row.get("kind")
    if not file_path:
        return None
    if kind == "file":
        return None
    start_line = row.get("start_line")
    end_line = row.get("end_line")
    if start_line is None or end_line is None:
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
    return max(1, math.ceil(len(text) / 2.7))


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    cut = normalized[: max(0, limit - 3)].rsplit(" ", 1)[0].rstrip()
    return f"{cut or normalized[: max(0, limit - 3)]}..."


def _strip_source(node: ContextNode) -> ContextNode:
    reason = [item for item in node.reason if item != "raw_code_fallback"] or list(node.reason)
    return ContextNode(
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
            " ".join(v for v in (node.name, node.path, node.summary) if v)
        ),
        reason=reason,
        reason_details=node.reason_details,
        explanation=build_explanation(reason) if node.explanation is not None else None,
        source_omitted_reason="token_budget",
    )


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def _indexed_file_count(index: ProjectIndex) -> int:
    row = index.conn.execute("SELECT COUNT(*) AS count FROM nodes WHERE type = 'file'").fetchone()
    return int(row["count"] if row is not None else 0)


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
            candidate = _strip_source(node)
        if used + candidate.estimated_tokens <= max_tokens:
            selected.append(candidate)
            used += candidate.estimated_tokens
    return selected
