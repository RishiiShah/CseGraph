"""Task-level execution for agent-mode adaptive benchmarks."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Mapping, Sequence

from tools.benchmarks.models import AdaptiveBenchmarkTask, CommandResult, TaskExecutionResult


def execute_benchmark_task(
    task: AdaptiveBenchmarkTask,
    repo: Path,
    *,
    agent_command: Sequence[str] | None = None,
    allow_network: bool = False,
    environment: Mapping[str, str] | None = None,
) -> TaskExecutionResult:
    if not task.supported:
        return TaskExecutionResult(
            status="unsupported",
            reason=task.unsupported_reason or "task is marked unsupported",
        )
    if task.execution_mode != "agent":
        return TaskExecutionResult(status="retrieval_only")
    if task.network_required and not allow_network:
        return TaskExecutionResult(status="skipped", reason="network access is disabled")

    command = tuple(agent_command or task.agent_command)
    if not command:
        return TaskExecutionResult(status="skipped", reason="no agent command was supplied")
    before = set(_git_changed_files(repo))
    variables = {
        "task": task.task,
        "target": task.target or "",
        "repo": str(repo),
        "task_id": task.id,
    }
    setup = _run_task_command(
        task.setup_command, repo, task.timeout_seconds, environment, variables
    )
    if setup is not None and setup.returncode != 0:
        return TaskExecutionResult(status="setup_failed", setup=setup)
    agent = _run_task_command(command, repo, task.timeout_seconds, environment, variables)
    if agent is None or agent.returncode != 0:
        return TaskExecutionResult(status="agent_failed", setup=setup, agent=agent)
    test = _run_task_command(task.test_command, repo, task.timeout_seconds, environment, variables)
    hidden = tuple(
        result
        for check in task.hidden_checks
        if (result := _run_task_command(check, repo, task.timeout_seconds, environment, variables))
        is not None
    )
    changed = tuple(sorted(set(_git_changed_files(repo)) - before))
    permitted_ok = set(changed).issubset(set(task.permitted_files))
    passed = (
        (test is None or test.returncode == 0)
        and all(result.returncode == 0 for result in hidden)
        and permitted_ok
    )
    return TaskExecutionResult(
        status="passed" if passed else "failed",
        setup=setup,
        agent=agent,
        test=test,
        hidden_checks=hidden,
        changed_files=changed,
        permitted_files_ok=permitted_ok,
    )


def _git_changed_files(repo: Path) -> tuple[str, ...]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain=v1", "-z"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if result.returncode != 0:
        return ()
    paths: list[str] = []
    for entry in result.stdout.decode("utf-8", errors="replace").split("\0"):
        if not entry:
            continue
        path = entry[3:] if len(entry) > 3 else entry
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path)
    return tuple(sorted(set(paths)))


def _run_task_command(
    command: Sequence[str],
    repo: Path,
    timeout_seconds: float,
    environment: Mapping[str, str] | None,
    variables: Mapping[str, str],
) -> CommandResult | None:
    if not command:
        return None
    argv = tuple(part.format_map(variables) for part in command)
    env = os.environ.copy()
    if environment:
        env.update(environment)
    started = time.perf_counter()
    try:
        result = subprocess.run(
            argv,
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            argv=argv,
            returncode=None,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or ""),
            skipped_reason="timeout",
        )
    except OSError as exc:
        return CommandResult(
            argv=argv,
            returncode=None,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            stderr=str(exc),
            skipped_reason="command_unavailable",
        )
    return CommandResult(
        argv=argv,
        returncode=result.returncode,
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
        stdout=result.stdout[-20_000:],
        stderr=result.stderr[-20_000:],
    )
