from __future__ import annotations

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
    ) -> ContextResult:
        config = get_profile(profile)
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

            context_nodes: List[ContextNode] = []
            for node_id in context_ids:
                row = symbols[node_id]
                raw_evidence = evidence.get(node_id, [])
                lineage = sorted({e for e in raw_evidence if e.startswith("expanded-from-")})
                clean_evidence = sorted({e for e in raw_evidence if not e.startswith("expanded-from-")})
                context_nodes.append(
                    ContextNode(
                        node_id=node_id,
                        kind=row["kind"],
                        name=row["name"],
                        file_path=row["file_path"],
                        start_line=row["start_line"],
                        end_line=row["end_line"],
                        score=round(scores.get(node_id, 0.0), 4),
                        raw_code=node_id in raw_nodes,
                        evidence=clean_evidence,
                        summary=summaries.get(node_id, ""),
                        lineage=lineage,
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
