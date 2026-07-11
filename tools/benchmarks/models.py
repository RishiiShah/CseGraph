"""Data models shared by adaptive benchmark components."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class BenchmarkTargetExpectation:
    path: str
    line: int
    name: str | None = None
    id: str | None = None


@dataclass(frozen=True)
class BenchmarkEvidenceExpectation:
    path: str
    line: int
    role: str | None = None


@dataclass(frozen=True)
class BenchmarkPermittedRange:
    path: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class AdaptiveBenchmarkTask:
    id: str
    repo: str
    commit: str
    category: str
    task: str
    target: str | None = None
    expected_status: str = "ready"
    expected_target: BenchmarkTargetExpectation | None = None
    expected_candidates: tuple[BenchmarkTargetExpectation, ...] = ()
    required_evidence: tuple[BenchmarkEvidenceExpectation, ...] = ()
    permitted_ranges: tuple[BenchmarkPermittedRange, ...] = ()
    expected_next_tool: str | None = None
    expected_locations: tuple[str, ...] = ()
    permitted_files: tuple[str, ...] = ()
    setup_command: tuple[str, ...] = ()
    test_command: tuple[str, ...] = ()
    hidden_checks: tuple[tuple[str, ...], ...] = ()
    execution_mode: str = "retrieval"
    agent_command: tuple[str, ...] = ()
    timeout_seconds: float = 120.0
    supported: bool = True
    unsupported_reason: str | None = None
    network_required: bool = False


@dataclass(frozen=True)
class AdaptiveBenchmarkCorpus:
    path: Path
    schema_version: str
    version: str
    tier: str
    status: str
    unsupported_reason: str | None
    repositories: Mapping[str, "BenchmarkRepository"]
    tasks: tuple[AdaptiveBenchmarkTask, ...]


@dataclass(frozen=True)
class BenchmarkRepository:
    path: str
    url: str
    commit: str


@dataclass(frozen=True)
class PreparedRepository:
    path: Path | None
    observed_commit: str | None
    commit_matches: bool
    bootstrapped: bool
    reason: str | None = None


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int | None
    duration_ms: float
    stdout: str = ""
    stderr: str = ""
    skipped_reason: str | None = None


@dataclass(frozen=True)
class TaskExecutionResult:
    status: str
    setup: CommandResult | None = None
    agent: CommandResult | None = None
    test: CommandResult | None = None
    hidden_checks: tuple[CommandResult, ...] = ()
    changed_files: tuple[str, ...] = ()
    permitted_files_ok: bool = True
    reason: str | None = None


@dataclass
class BaselineSlice:
    path: str
    lines: list[int]
    role: str
    code: str


@dataclass
class BaselineResult:
    schema_version: str
    query: str
    target: str | None
    slices: list[BaselineSlice]
    usage: dict[str, Any]
    discovery: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


__all__ = [
    "AdaptiveBenchmarkCorpus",
    "AdaptiveBenchmarkTask",
    "BaselineResult",
    "BaselineSlice",
    "BenchmarkEvidenceExpectation",
    "BenchmarkPermittedRange",
    "BenchmarkRepository",
    "BenchmarkTargetExpectation",
    "CommandResult",
    "PreparedRepository",
    "TaskExecutionResult",
]
