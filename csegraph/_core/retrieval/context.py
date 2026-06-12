from __future__ import annotations

import heapq
import itertools
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from csegraph._core.config.profiles import load_profile
from csegraph._core.core.models import ContextNode, ContextResult, SufficiencyResult
from csegraph._core.cse.metrics import (
    SufficiencyMetrics,
    all_pass,
    compute_metrics,
    raw_code_nodes,
)
from csegraph._core.index.loaders import load_edge_maps, load_summaries, load_symbols
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.retrieval.explain import (
    build_explanation,
    build_reason_details,
    normalize_reasons,
)
from csegraph._core.retrieval.target_resolution import TargetResolution, resolve_target
from csegraph._core.retrieval.helpers import is_small_helper_row
from csegraph._core.retrieval.scoring import apply_graph_expansion, fts_lexical_scores, lexical_scores
from csegraph._core.text.source_reader import read_source_lines


DETAIL_LEVELS = {"auto", "minimal", "standard", "full"}
MINIMAL_NODE_LIMIT = 5
MINIMAL_SUMMARY_CHAR_LIMIT = 240
DIRECT_CALL_ALWAYS_LIMIT = 2
DIRECT_CALL_SCORE_FLOOR = 4.0
RANKED_SCORE_FLOOR = 3.0
RANKED_SCORE_RATIO_FLOOR = 0.30


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
            config = load_profile(profile, config_path=config_path, repo_root=repo_root)

            t0 = time.perf_counter()
            symbols = load_symbols(index, exclude_heavy=True)
            summaries = load_summaries(index)
            outgoing, incoming = load_edge_maps(index)
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
            anchors = [target_id] if target_id else [
                node_id for node_id, _ in heapq.nlargest(config.top_k, scores.items(), key=lambda item: item[1])
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
            timings["graph_expansion"] = _elapsed_ms(t0)

            context_ids = _select_context_ids(
                target_id,
                scores,
                evidence,
                symbols,
                outgoing,
                config.context_budget,
            )
            metrics = compute_metrics(task, target_id, context_ids, symbols, summaries, outgoing)
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
                context_ids=context_ids,
                target_id=target_id,
                include_source=include_source,
                explain=explain or returned_detail_level == "full",
                max_tokens=max_tokens,
                task=task,
                config=config,
                repo_root=repo_root,
                symbols=symbols,
                summaries=summaries,
                evidence=evidence,
                scores=scores,
                outgoing=outgoing,
                incoming=incoming,
                raw_nodes=raw_nodes,
                index=index,
            )

            # Auto should judge the compact response it will actually return before
            # promoting to standard; the full candidate pool is intentionally noisy.
            if detail_level == "auto" and not sufficient:
                nodes, metrics, sufficient = _build_detail_pass(
                    detail_level="standard",
                    context_ids=context_ids,
                    target_id=target_id,
                    include_source=include_source,
                    explain=explain,
                    max_tokens=max_tokens,
                    task=task,
                    config=config,
                    repo_root=repo_root,
                    symbols=symbols,
                    summaries=summaries,
                    evidence=evidence,
                    scores=scores,
                    outgoing=outgoing,
                    incoming=incoming,
                    raw_nodes=raw_nodes,
                    index=index,
                )
                returned_detail_level = "standard"
            timings["detail_pass"] = _elapsed_ms(t0)

            final_raw_nodes = {node.id for node in nodes if node.raw_code}
            estimated_tokens = sum(node.estimated_tokens for node in nodes)

            # Aggregate confidence tiers for edges among the returned context nodes.
            confidence_counts: Dict[str, int] = {}
            selected_ids = {node.id for node in nodes}
            for nid in selected_ids:
                for edge in outgoing.get(nid, []):
                    tgt = edge.get("target_id") or edge.get("target")
                    if tgt in selected_ids:
                        tier = edge.get("confidence_tier") or "EXTRACTED"
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
                    },
                ),
                total_estimated_tokens=estimated_tokens,
                nodes=nodes,
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
) -> List[ContextNode]:
    nodes: List[ContextNode] = []
    target_row = symbols.get(target_id, {})
    for node_id in context_ids:
        row = symbols[node_id]
        raw_evidence = evidence.get(node_id, [])
        lineage = sorted({e for e in raw_evidence if e.startswith("expanded-from-")})
        clean_evidence = sorted({e for e in raw_evidence if not e.startswith("expanded-from-")})
        raw_summary = summaries.get(node_id, "")
        summary = _truncate_text(raw_summary, MINIMAL_SUMMARY_CHAR_LIMIT) if returned_detail_level == "minimal" else raw_summary
        source_text = _read_node_source(repo_root, row) if node_id in source_ids else None
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
            )
        )
    return nodes


def _resolve_target(
    target: Optional[str],
    task: str,
    symbols: Dict[str, Dict[str, Any]],
    summaries: Dict[str, str],
    index: Optional[ProjectIndex] = None,
    repo_root: str = "",
) -> str:
    """Backward-compatible helper returning a single resolved node id."""
    resolution = resolve_target(target, task, symbols, summaries, index, repo_root=repo_root)
    if resolution.status == "ambiguous":
        raise ValueError(
            f"Target '{resolution.requested}' matched {len(resolution.candidates)} symbols; "
            "pass a qualified name or node id."
        )
    if resolution.status == "unresolved":
        label = resolution.requested or target or ""
        raise ValueError(f"Target '{label}' did not match any indexed symbol.")
    return resolution.target_id


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
            "Re-run csegraph_context with a specific target from target_candidates."
        ],
        confidence_breakdown={},
        timings_ms=timings,
    )


def _select_context_ids(
    target_id: str,
    scores: Dict[str, float],
    evidence: Dict[str, List[str]],
    symbols: Dict[str, Dict[str, Any]],
    outgoing: Dict[str, List[Dict[str, Any]]],
    budget: int,
) -> List[str]:
    baseline_score = 0.01
    adaptive_budget = min(budget, max(MINIMAL_NODE_LIMIT, math.ceil(budget * 0.90)))
    direct_calls: List[str] = []
    for edge in outgoing.get(target_id, []):
        if edge["relation"] == "calls":
            direct_calls.append(edge["target_id"])
    ranked_direct_calls = sorted(
        dict.fromkeys(direct_calls),
        key=lambda node_id: (-scores.get(node_id, 0.0), node_id),
    )
    required = [target_id]
    for index, node_id in enumerate(ranked_direct_calls):
        if (
            index < DIRECT_CALL_ALWAYS_LIMIT
            or scores.get(node_id, 0.0) >= DIRECT_CALL_SCORE_FLOOR
            or _has_lexical_evidence(evidence.get(node_id, []))
        ):
            required.append(node_id)
    selected: List[str] = []
    seen: set[str] = set()
    selected_by_path: Dict[str, int] = {}
    for node_id in required:
        if node_id in scores and node_id not in seen and len(selected) < adaptive_budget:
            path = str(symbols.get(node_id, {}).get("file_path") or "")
            if (
                node_id != target_id
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
    remaining = adaptive_budget - len(selected)
    if remaining > 0:
        top_score = max(scores.values(), default=0.0)
        ranked_floor = max(RANKED_SCORE_FLOOR, top_score * RANKED_SCORE_RATIO_FLOOR)
        for node_id, _ in heapq.nlargest(budget, scores.items(), key=lambda item: item[1]):
            if node_id not in seen:
                if scores.get(node_id, 0.0) <= baseline_score:
                    continue
                if scores.get(node_id, 0.0) < ranked_floor and len(selected) >= MINIMAL_NODE_LIMIT:
                    continue
                selected.append(node_id)
                seen.add(node_id)
                if len(selected) >= adaptive_budget:
                    break
    return selected


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
    )


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
        if is_small_helper_row(symbols.get(node_id, {}))
    }
    return ({target_id} | set(raw_nodes) | direct_calls | small_helpers) & context_set


def _next_actions(
    returned_detail_level: str,
    target_id: str,
) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    if returned_detail_level == "minimal":
        actions.append({
            "action": "expand_context",
            "detail_level": "standard",
            "reason": "Request working context with selected source before editing.",
        })
    if target_id:
        actions.append({
            "action": "inspect_graph",
            "tool": "csegraph_graph",
            "node": target_id,
            "reason": "Inspect graph neighbors when blast radius or dependencies matter.",
        })
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


def _build_detail_pass(
    *,
    detail_level: str,
    context_ids: Sequence[str],
    target_id: str,
    include_source: str,
    explain: bool,
    max_tokens: Optional[int],
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
        detail_level, include_source, target_id, response_ids,
        outgoing, symbols, raw_nodes,
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
    )
    nodes = _apply_token_budget(nodes, max_tokens)
    retained_ids = [node.id for node in nodes]
    metrics = compute_metrics(task, target_id, retained_ids, symbols, summaries, outgoing)
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
        try:
            full_path = (Path(repo_root).resolve() / file_path).resolve()
            if not full_path.is_relative_to(Path(repo_root).resolve()):
                return None
            return full_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
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
        id=node.id, kind=node.kind, name=node.name, path=node.path,
        line_range=node.line_range, score=node.score, language=node.language,
        raw_code=False, evidence=node.evidence, summary=node.summary,
        lineage=node.lineage, source_text=None,
        estimated_tokens=_estimate_tokens(" ".join(v for v in (node.name, node.path, node.summary) if v)),
        reason=reason,
        explanation=build_explanation(reason) if node.explanation is not None else None,
    )


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


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
