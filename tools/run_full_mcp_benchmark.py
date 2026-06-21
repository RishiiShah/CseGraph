#!/usr/bin/env python3
"""Run the full native-MCP benchmark suite.

Performance benchmarks run against repositories under ``sandbox/`` and call the
CseGraph MCP server through stdio. The self-repo corpus check is kept as a
CseGraph-specific quality regression because its expectations name CseGraph
symbols.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from cross_repo_benchmark import NativeMcpClient, server_command_from_env

REPO = Path(__file__).resolve().parents[1]
PYTHON = REPO / "env" / "bin" / "python"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

SCRATCH = REPO / ".scratch" / "csegraph"
CROSS_REPO_TOOL = REPO / "tools" / "cross_repo_benchmark.py"
CORPUS_TOOL = REPO / "tools" / "check_benchmark_regression.py"
CORPUS = REPO / "benchmarks" / "context_quality" / "csegraph_self.json"
SANDBOX = REPO / "sandbox"

PROFILES = tuple(
    item.strip()
    for item in os.environ.get("CSEGRAPH_FULL_BENCH_PROFILES", "auto").split(",")
    if item.strip()
)
SANDBOX_REPOS = ",".join(
    item.strip()
    for item in os.environ.get(
        "CSEGRAPH_BENCH_REPOS",
        "nanoGPT,micrograd,django,pandas,flask,transformers,scikit-learn,fastapi,celery,pytest",
    ).split(",")
    if item.strip()
)
QUERY_LIMIT = os.environ.get("CSEGRAPH_BENCH_QUERY_LIMIT", "100")

WORKFLOWS: tuple[dict[str, Any], ...] = (
    {
        "id": "implement-change",
        "task": "implement or fix code using graph-backed context",
        "max_tool_calls": 2,
        "steps": (
            ("csegraph_minimal", {}),
            ("csegraph_context", {"detail_level": "auto", "include_source": "auto"}),
        ),
    },
    {
        "id": "pre-merge-check",
        "task": "assess merge readiness and highest-risk areas",
        "max_tool_calls": 3,
        "steps": (
            ("csegraph_minimal", {}),
            ("csegraph_context", {"detail_level": "auto", "include_source": "never"}),
            ("csegraph_graph", {"depth": 1, "detail_level": "minimal"}),
        ),
    },
    {
        "id": "debug-issue",
        "task": "debug a failing test or runtime error",
        "max_tool_calls": 3,
        "steps": (
            ("csegraph_minimal", {}),
            ("csegraph_context", {"detail_level": "standard", "include_source": "auto"}),
            ("csegraph_graph", {"depth": 1, "detail_level": "minimal"}),
        ),
    },
)


def run_command(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 3600,
    include_stdout: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    result = subprocess.run(
        cmd,
        cwd=REPO,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    payload = {
        "command": cmd,
        "returncode": result.returncode,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }
    if include_stdout:
        payload["stdout"] = result.stdout
    return payload


def run_cross_repo(profile: str) -> dict[str, Any]:
    report = SCRATCH / f"native_mcp_sandbox_{profile}.md"
    json_path = SCRATCH / f"native_mcp_sandbox_{profile}.json"
    env = {
        "CSEGRAPH_BENCH_PROFILE": profile,
        "CSEGRAPH_BENCH_REPOS": SANDBOX_REPOS,
        "CSEGRAPH_BENCH_QUERY_LIMIT": QUERY_LIMIT,
        "CSEGRAPH_CROSS_REPO_REPORT": str(report),
        "CSEGRAPH_CROSS_REPO_JSON": str(json_path),
    }
    print(f"[sandbox:{profile}] MCP cross-repo benchmark...", flush=True)
    execution = run_command([str(PYTHON), str(CROSS_REPO_TOOL)], env=env, timeout=7200)
    payload = read_json(json_path) if execution["returncode"] == 0 else None
    return {
        "kind": "native-mcp-sandbox-cross-repo",
        "profile": profile,
        "workload_root": str(SANDBOX),
        "repos": SANDBOX_REPOS.split(","),
        "query_limit_per_repo": int(QUERY_LIMIT),
        "report_path": str(report),
        "json_path": str(json_path),
        "execution": execution,
        "result": payload,
    }


def run_corpus(profile: str) -> dict[str, Any]:
    print(f"[self-corpus:{profile}] MCP quality regression...", flush=True)
    db = SCRATCH / f"benchmark-mcp-corpus-{profile}.db"
    cmd = [
        str(PYTHON),
        str(CORPUS_TOOL),
        "--repo",
        str(REPO),
        "--db",
        str(db),
        "--corpus",
        str(CORPUS),
        "--profile",
        profile,
    ]
    if profile == "medium":
        cmd.extend(
            [
                "--max-avg-context-tokens",
                "4000",
                "--max-avg-response-bytes",
                "70000",
                "--max-returned-node-count",
                "60",
            ]
        )
    elif profile == "large":
        cmd.extend(
            [
                "--max-avg-context-tokens",
                "5000",
                "--max-avg-response-bytes",
                "80000",
                "--max-returned-node-count",
                "80",
            ]
        )
    execution = run_command(cmd, timeout=600, include_stdout=True)
    payload = parse_json_stdout(execution["stdout"]) if execution["returncode"] == 0 else None
    execution.pop("stdout", None)
    return {
        "kind": "native-mcp-self-corpus-quality-regression",
        "profile": profile,
        "workload_root": str(REPO),
        "corpus": str(CORPUS),
        "db": str(db),
        "execution": execution,
        "result": payload,
    }


def run_agent_workflows(profile: str) -> dict[str, Any]:
    repo_name = os.environ.get("CSEGRAPH_WORKFLOW_REPO", SANDBOX_REPOS.split(",")[0])
    repo = SANDBOX / repo_name
    db = repo / ".csegraph" / f"workflow-{profile}.db"
    print(f"[workflow:{profile}] MCP agent workflows on {repo_name}...", flush=True)
    started = time.perf_counter()
    try:
        result = asyncio.run(run_agent_workflows_async(repo, db, profile=profile))
    except Exception as exc:
        return {
            "kind": "native-mcp-sandbox-agent-workflows",
            "profile": profile,
            "workload_root": str(repo),
            "returncode": 1,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": str(exc),
        }
    return {
        "kind": "native-mcp-sandbox-agent-workflows",
        "profile": profile,
        "workload_root": str(repo),
        "returncode": 0,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "result": result,
    }


async def run_agent_workflows_async(repo: Path, db: Path, *, profile: str) -> dict[str, Any]:
    command, args = server_command_from_env()
    async with NativeMcpClient(command, args) as client:
        await client.call_tool(
            "csegraph_index",
            {
                "repo": str(repo),
                "db": str(db),
                "profile": profile,
                "postprocess_level": os.environ.get("CSEGRAPH_BENCH_POSTPROCESS_LEVEL", "minimal"),
            },
        )
        workflow_results = []
        for workflow in WORKFLOWS:
            workflow_results.append(await run_one_workflow(client, repo, db, profile, workflow))
        return {
            "transport": "MCP stdio via mcp.client ClientSession.call_tool",
            "repo": str(repo),
            "db": str(db),
            "profile": profile,
            "workflows": workflow_results,
        }


async def run_one_workflow(
    client: NativeMcpClient,
    repo: Path,
    db: Path,
    profile: str,
    workflow: dict[str, Any],
) -> dict[str, Any]:
    graph_node: str | None = None
    calls: list[dict[str, Any]] = []
    for tool_name, step_args in workflow["steps"]:
        if len(calls) >= workflow["max_tool_calls"]:
            break
        arguments = {
            "repo": str(repo),
            "db": str(db),
            "profile": profile,
            "task": workflow["task"],
            **step_args,
        }
        if tool_name == "csegraph_graph":
            if graph_node is None:
                calls.append({"tool": tool_name, "skipped": "no graph target after context"})
                continue
            arguments["node"] = graph_node
        metrics = await client.call_tool(tool_name, arguments)
        payload = parse_json_stdout(metrics.content_text) or {"raw": metrics.content_text}
        if tool_name == "csegraph_context":
            graph_node = pick_graph_target(payload)
        calls.append(
            {
                "tool": tool_name,
                "latency_ms": metrics.latency_ms,
                "content_bytes": metrics.content_bytes,
                "content_chars4_tokens": metrics.content_chars4_tokens,
                "content_openai_o200k_tokens": metrics.content_openai_o200k_tokens,
                "schema_version": payload.get("schema_version")
                if isinstance(payload, dict)
                else None,
                "sufficient": payload.get("sufficiency", {}).get("sufficient")
                if isinstance(payload, dict) and isinstance(payload.get("sufficiency"), dict)
                else None,
            }
        )
    tool_calls = len([call for call in calls if "skipped" not in call])
    return {
        "id": workflow["id"],
        "task": workflow["task"],
        "tool_calls": tool_calls,
        "max_tool_calls": workflow["max_tool_calls"],
        "within_turn_budget": tool_calls <= workflow["max_tool_calls"],
        "calls": calls,
    }


def pick_graph_target(payload: dict[str, Any]) -> str | None:
    target = payload.get("target")
    if isinstance(target, str) and target.strip():
        return target.strip()
    if isinstance(target, dict):
        target_id = target.get("id")
        if isinstance(target_id, str) and target_id.strip():
            return target_id.strip()
    nodes = payload.get("symbols") or payload.get("nodes")
    if isinstance(nodes, list) and nodes:
        first = nodes[0]
        if isinstance(first, dict):
            node_id = first.get("id")
            if isinstance(node_id, str) and node_id.strip():
                return node_id.strip()
    return None


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def parse_json_stdout(stdout: str) -> dict[str, Any] | None:
    start = stdout.find("{")
    if start == -1:
        return None
    try:
        return json.loads(stdout[start:])
    except json.JSONDecodeError:
        return None


def main() -> int:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "transport": "native MCP stdio via mcp.client",
        "performance_workload": str(SANDBOX),
        "sdk_policy": (
            "SDK/internal BenchmarkService remains available for maintainer diagnostics; "
            "this orchestrator does not use it for performance benchmarks."
        ),
        "profiles": list(PROFILES),
        "sandbox": {},
        "agent_workflows": {},
        "self_corpus_quality": {},
    }

    failed = False
    for profile in PROFILES:
        sandbox_result = run_cross_repo(profile)
        results["sandbox"][profile] = sandbox_result
        failed = failed or sandbox_result["execution"]["returncode"] != 0

    for profile in PROFILES:
        corpus_result = run_corpus(profile)
        results["self_corpus_quality"][profile] = corpus_result
        failed = failed or corpus_result["execution"]["returncode"] != 0

    for profile in PROFILES:
        workflow_result = run_agent_workflows(profile)
        results["agent_workflows"][profile] = workflow_result
        failed = failed or workflow_result["returncode"] != 0

    out_path = SCRATCH / "benchmark_results.json"
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nResults written to {out_path}")
    json.dump(results, sys.stdout, indent=2, sort_keys=True)
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
