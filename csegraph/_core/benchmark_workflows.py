"""Agent workflow benchmarks — multi-step MCP paths for code-change tasks.

Simulates the context loop agents should follow (minimal → context → optional graph)
and records estimated tokens, response bytes, and tool-call counts per workflow.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from csegraph._core.core.models import BenchmarkResult, BenchmarkStep


def estimate_json_tokens(payload: Any) -> int:
    return max(1, len(json.dumps(payload, default=str)) // 4)


@dataclass(frozen=True)
class AgentWorkflowSpec:
    id: str
    task: str
    max_tool_calls: int
    steps: tuple[tuple[str, dict[str, Any]], ...]


# Mirrors MCP workflow prompts; stays within the ≤3 tool calls per turn rule.
AGENT_WORKFLOW_SPECS: tuple[AgentWorkflowSpec, ...] = (
    AgentWorkflowSpec(
        id="implement-change",
        task="implement or fix code using graph-backed context",
        max_tool_calls=2,
        steps=(
            ("csegraph_minimal", {}),
            (
                "csegraph_context",
                {"detail_level": "auto", "include_source": "auto"},
            ),
        ),
    ),
    AgentWorkflowSpec(
        id="pre-merge-check",
        task="assess merge readiness and highest-risk areas",
        max_tool_calls=3,
        steps=(
            ("csegraph_minimal", {}),
            ("csegraph_context", {"detail_level": "auto", "include_source": "never"}),
            ("csegraph_graph", {"depth": 1, "detail_level": "minimal"}),
        ),
    ),
    AgentWorkflowSpec(
        id="debug-issue",
        task="debug a failing test or runtime error",
        max_tool_calls=3,
        steps=(
            ("csegraph_minimal", {}),
            ("csegraph_context", {"detail_level": "standard", "include_source": "auto"}),
        ),
    ),
)


def run_agent_workflow_benchmarks(
    repo: str | Path,
    db_path: str | Path,
    *,
    profile: str = "medium",
    handle_tool: Callable[[str, dict[str, Any]], Any],
    reset_session: Callable[[], None],
    ensure_indexed: Callable[[], None] | None = None,
) -> BenchmarkResult:
    """Run standard agent workflows through the MCP tool handlers."""
    from csegraph._core.server.app import _db_path

    repo_root = str(Path(repo).resolve())
    db = str(Path(db_path))
    base_args = {"repo": repo_root, "db": db, "profile": profile}

    total_start = time.perf_counter()
    steps: list[BenchmarkStep] = []

    if ensure_indexed is not None:
        ensure_indexed()

    for spec in AGENT_WORKFLOW_SPECS:
        reset_session()
        workflow_tokens = 0
        workflow_bytes = 0
        tool_calls = 0
        graph_node: str | None = None

        for tool_name, step_args in spec.steps:
            if tool_calls >= spec.max_tool_calls:
                break
            arguments = {**base_args, "task": spec.task, **step_args}
            if tool_name == "csegraph_graph":
                node = graph_node or step_args.get("node")
                if not node:
                    steps.append(
                        BenchmarkStep(
                            name=f"workflow:{spec.id}:{tool_name}",
                            elapsed_ms=0.0,
                            stats={"skipped": "no graph target after context"},
                        )
                    )
                    continue
                arguments["node"] = node

            start = time.perf_counter()
            try:
                payload = handle_tool(tool_name, arguments)
            except Exception as exc:
                elapsed = _elapsed_ms(start)
                steps.append(
                    BenchmarkStep(
                        name=f"workflow:{spec.id}:{tool_name}",
                        elapsed_ms=elapsed,
                        stats={"error": str(exc)},
                    )
                )
                break

            elapsed = _elapsed_ms(start)
            tool_calls += 1
            if not isinstance(payload, dict):
                payload = {"result": payload}
            resp_bytes = len(json.dumps(payload, default=str).encode("utf-8"))
            tokens = estimate_json_tokens(payload)
            workflow_tokens += tokens
            workflow_bytes += resp_bytes

            if tool_name == "csegraph_context":
                graph_node = _pick_graph_target(payload)

            steps.append(
                BenchmarkStep(
                    name=f"workflow:{spec.id}:{tool_name}",
                    elapsed_ms=elapsed,
                    stats={
                        "estimated_tokens": tokens,
                        "mcp_response_bytes": resp_bytes,
                        "schema_version": payload.get("schema_version"),
                        "sufficient": (payload.get("sufficiency") or {}).get("sufficient")
                        if isinstance(payload.get("sufficiency"), dict)
                        else None,
                    },
                )
            )

            if tool_name == "csegraph_context" and spec.id == "debug-issue" and graph_node:
                graph_args = {
                    **base_args,
                    "node": graph_node,
                    "depth": 1,
                    "detail_level": "minimal",
                }
                if tool_calls < spec.max_tool_calls:
                    start = time.perf_counter()
                    try:
                        graph_payload = handle_tool("csegraph_graph", graph_args)
                    except Exception as exc:
                        steps.append(
                            BenchmarkStep(
                                name=f"workflow:{spec.id}:csegraph_graph",
                                elapsed_ms=_elapsed_ms(start),
                                stats={"error": str(exc)},
                            )
                        )
                    else:
                        tool_calls += 1
                        if isinstance(graph_payload, dict):
                            workflow_bytes += len(
                                json.dumps(graph_payload, default=str).encode("utf-8")
                            )
                            workflow_tokens += estimate_json_tokens(graph_payload)
                        steps.append(
                            BenchmarkStep(
                                name=f"workflow:{spec.id}:csegraph_graph",
                                elapsed_ms=_elapsed_ms(start),
                                stats={
                                    "estimated_tokens": estimate_json_tokens(graph_payload),
                                    "mcp_response_bytes": len(
                                        json.dumps(graph_payload, default=str).encode("utf-8")
                                    ),
                                },
                            )
                        )

        steps.append(
            BenchmarkStep(
                name=f"workflow:{spec.id}:summary",
                elapsed_ms=0.0,
                stats={
                    "tool_calls": tool_calls,
                    "max_tool_calls": spec.max_tool_calls,
                    "total_estimated_tokens": workflow_tokens,
                    "total_mcp_response_bytes": workflow_bytes,
                    "within_turn_budget": tool_calls <= spec.max_tool_calls,
                },
            )
        )

    return BenchmarkResult(
        command="benchmark-agent-workflows",
        db_path=db,
        repo_root=repo_root,
        profile=profile,
        query="agent-workflows",
        target=None,
        graph_output_path=str(_db_path(repo_root, db)),
        total_elapsed_ms=_elapsed_ms(total_start),
        steps=steps,
    )


def _pick_graph_target(context_payload: dict[str, Any]) -> str | None:
    target = context_payload.get("target")
    if isinstance(target, str) and target.strip():
        return target.strip()
    nodes = context_payload.get("nodes")
    if isinstance(nodes, list) and nodes:
        first = nodes[0]
        if isinstance(first, dict):
            node_id = first.get("id")
            if isinstance(node_id, str) and node_id.strip():
                return node_id.strip()
    return None


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)
