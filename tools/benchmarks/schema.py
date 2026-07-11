"""Schema parsing and validation for adaptive benchmark corpora."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Mapping

from tools.benchmarks.models import (
    AdaptiveBenchmarkCorpus,
    AdaptiveBenchmarkTask,
    BenchmarkEvidenceExpectation,
    BenchmarkPermittedRange,
    BenchmarkRepository,
    BenchmarkTargetExpectation,
)

TASK_SCHEMA_VERSION_V1 = "csegraph-adaptive-benchmark-v1"
TASK_SCHEMA_VERSION_V2 = "csegraph-adaptive-benchmark-v2"
TASK_SCHEMA_VERSIONS = {TASK_SCHEMA_VERSION_V1, TASK_SCHEMA_VERSION_V2}
TASK_CATEGORIES = frozenset(
    {
        "definition",
        "ambiguous",
        "cross-file",
        "debug",
        "refactor",
        "structural",
        "test-impact",
    }
)


def load_corpus(
    source: str | Path | Mapping[str, Any],
    *,
    path: Path | None = None,
) -> AdaptiveBenchmarkCorpus:
    if isinstance(source, Mapping):
        corpus_path = path or Path("<generated>")
        payload = dict(source)
    else:
        corpus_path = Path(source)
        payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    schema_version = str(payload.get("schema_version") or "")
    if schema_version not in TASK_SCHEMA_VERSIONS:
        raise ValueError(
            f"Expected benchmark schema in {sorted(TASK_SCHEMA_VERSIONS)!r}, "
            f"got {payload.get('schema_version')!r}"
        )
    tier = str(payload.get("tier") or "pr")
    if tier not in {"pr", "nightly", "release", "perf", "broad"}:
        raise ValueError(f"Unsupported benchmark tier {tier!r}")
    status = str(payload.get("status") or "ready")
    if status not in {"ready", "planned", "blocked"}:
        raise ValueError(f"Unsupported benchmark status {status!r}")
    repositories: dict[str, BenchmarkRepository] = {}
    for repo_path, raw_repository in (payload.get("repositories") or {}).items():
        if not isinstance(raw_repository, dict):
            raise ValueError(f"Repository {repo_path!r} must be an object")
        try:
            repository = BenchmarkRepository(
                path=str(repo_path),
                url=str(raw_repository["url"]),
                commit=str(raw_repository["commit"]),
            )
        except KeyError as exc:
            raise ValueError(f"Repository {repo_path!r} is missing {exc.args[0]!r}") from exc
        if not repository.url or len(repository.commit) != 40:
            raise ValueError(f"Repository {repo_path!r} must have a URL and 40-char commit")
        _reject_csegraph_self_repository(repository.path, context=f"Repository {repo_path!r}")
        repositories[repository.path] = repository

    tasks: list[AdaptiveBenchmarkTask] = []
    required = {"id", "repo", "commit", "category", "task"}
    seen_ids: set[str] = set()
    for position, raw in enumerate(payload.get("tasks") or []):
        if not isinstance(raw, dict):
            raise ValueError(f"Task at index {position} must be an object")
        missing = required - raw.keys()
        if missing:
            raise ValueError(f"Task {raw.get('id', position)!r} is missing {sorted(missing)}")
        task_id = str(raw["id"])
        if task_id in seen_ids:
            raise ValueError(f"Duplicate benchmark task id {task_id!r}")
        seen_ids.add(task_id)
        execution_mode = str(raw.get("execution_mode") or "retrieval")
        if execution_mode not in {"retrieval", "agent"}:
            raise ValueError(f"Task {task_id!r} has unsupported execution_mode {execution_mode!r}")
        category = str(raw["category"])
        if category not in TASK_CATEGORIES:
            raise ValueError(f"Task {task_id!r} has unsupported category {category!r}")
        hidden_checks = _parse_hidden_checks(raw.get("hidden_checks", []), task_id)
        expected_status = str(raw.get("expected_status") or "ready")
        if expected_status not in {
            "ready",
            "ambiguous",
            "insufficient",
            "index_required",
            "refresh_required",
        }:
            raise ValueError(
                f"Task {task_id!r} has unsupported expected_status {expected_status!r}"
            )
        task = AdaptiveBenchmarkTask(
            id=task_id,
            repo=str(raw["repo"]),
            commit=str(raw["commit"]),
            category=category,
            task=str(raw["task"]),
            target=str(raw["target"]) if raw.get("target") is not None else None,
            expected_status=expected_status,
            expected_target=_parse_target_expectation(
                raw.get("expected_target"),
                task_id,
                "expected_target",
            ),
            expected_candidates=tuple(
                target
                for position, value in enumerate(raw.get("expected_candidates", []))
                if (
                    target := _parse_target_expectation(
                        value,
                        task_id,
                        f"expected_candidates[{position}]",
                    )
                )
                is not None
            ),
            required_evidence=_parse_evidence_expectations(
                raw.get("required_evidence", []),
                task_id,
            ),
            permitted_ranges=_parse_permitted_ranges(
                raw.get("permitted_ranges", []),
                task_id,
            ),
            expected_next_tool=(
                str(raw["expected_next_tool"])
                if raw.get("expected_next_tool") is not None
                else None
            ),
            expected_locations=tuple(str(v) for v in raw.get("expected_locations", [])),
            permitted_files=tuple(str(v) for v in raw.get("permitted_files", [])),
            setup_command=tuple(str(v) for v in raw.get("setup_command", [])),
            test_command=tuple(str(v) for v in raw.get("test_command", [])),
            hidden_checks=hidden_checks,
            execution_mode=execution_mode,
            agent_command=tuple(str(v) for v in raw.get("agent_command", [])),
            timeout_seconds=float(raw.get("timeout_seconds", 120.0)),
            supported=bool(raw.get("supported", True)),
            unsupported_reason=(
                str(raw["unsupported_reason"])
                if raw.get("unsupported_reason") is not None
                else None
            ),
            network_required=bool(raw.get("network_required", False)),
        )
        if len(task.commit) != 40:
            raise ValueError(f"Task {task.id!r} must pin a 40-character commit")
        if schema_version == TASK_SCHEMA_VERSION_V2:
            if task.expected_status == "ready" and task.expected_target is None:
                raise ValueError(f"Task {task.id!r} must define expected_target for ready status")
            if task.expected_status == "ambiguous" and len(task.expected_candidates) < 2:
                raise ValueError(f"Task {task.id!r} must define at least two expected_candidates")
            if task.expected_status == "ready" and not task.required_evidence:
                raise ValueError(f"Task {task.id!r} must define required_evidence for ready status")
            if not task.permitted_ranges:
                raise ValueError(f"Task {task.id!r} must define permitted_ranges")
            if task.category == "structural" and not task.expected_next_tool:
                raise ValueError(
                    f"Task {task.id!r} must define expected_next_tool for structural tasks"
                )
        task_repository = repositories.get(task.repo)
        _reject_csegraph_self_repository(task.repo, context=f"Task {task.id!r}")
        if task_repository is not None and task_repository.commit != task.commit:
            raise ValueError(f"Task {task.id!r} commit differs from repository {task.repo!r}")
        tasks.append(task)
    corpus_location = corpus_path if str(corpus_path).startswith("<") else corpus_path.resolve()
    return AdaptiveBenchmarkCorpus(
        path=corpus_location,
        schema_version=schema_version,
        version=str(payload.get("corpus_version") or "unversioned"),
        tier=tier,
        status=status,
        unsupported_reason=(
            str(payload["unsupported_reason"])
            if payload.get("unsupported_reason") is not None
            else None
        ),
        repositories=repositories,
        tasks=tuple(tasks),
    )


def corpus_to_payload(corpus: AdaptiveBenchmarkCorpus) -> dict[str, Any]:
    """Serialize an in-memory corpus only when a tool explicitly needs JSON."""

    payload: dict[str, Any] = {
        "schema_version": corpus.schema_version,
        "corpus_version": corpus.version,
        "tier": corpus.tier,
        "status": corpus.status,
        "repositories": {
            path: {"url": repository.url, "commit": repository.commit}
            for path, repository in corpus.repositories.items()
        },
        "tasks": [],
    }
    if corpus.unsupported_reason is not None:
        payload["unsupported_reason"] = corpus.unsupported_reason

    for task in corpus.tasks:
        raw: dict[str, Any] = {
            "id": task.id,
            "repo": task.repo,
            "commit": task.commit,
            "category": task.category,
            "task": task.task,
            "expected_status": task.expected_status,
        }
        if task.target is not None:
            raw["target"] = task.target
        if task.expected_target is not None:
            raw["expected_target"] = {
                "path": task.expected_target.path,
                "line": task.expected_target.line,
                **(
                    {"name": task.expected_target.name}
                    if task.expected_target.name is not None
                    else {}
                ),
                **({"id": task.expected_target.id} if task.expected_target.id is not None else {}),
            }
        if task.expected_candidates:
            raw["expected_candidates"] = [
                {
                    "path": candidate.path,
                    "line": candidate.line,
                    **({"name": candidate.name} if candidate.name is not None else {}),
                    **({"id": candidate.id} if candidate.id is not None else {}),
                }
                for candidate in task.expected_candidates
            ]
        raw["required_evidence"] = [
            {
                "path": evidence.path,
                "line": evidence.line,
                **({"role": evidence.role} if evidence.role is not None else {}),
            }
            for evidence in task.required_evidence
        ]
        raw["permitted_ranges"] = [
            {
                "path": permitted.path,
                "lines": [permitted.start_line, permitted.end_line],
            }
            for permitted in task.permitted_ranges
        ]
        if task.expected_next_tool is not None:
            raw["expected_next_tool"] = task.expected_next_tool
        if task.expected_locations:
            raw["expected_locations"] = list(task.expected_locations)
        if task.permitted_files:
            raw["permitted_files"] = list(task.permitted_files)
        if task.setup_command:
            raw["setup_command"] = list(task.setup_command)
        if task.test_command:
            raw["test_command"] = list(task.test_command)
        if task.hidden_checks:
            raw["hidden_checks"] = [list(check) for check in task.hidden_checks]
        if task.execution_mode != "retrieval":
            raw["execution_mode"] = task.execution_mode
        if task.agent_command:
            raw["agent_command"] = list(task.agent_command)
        if task.timeout_seconds != 120.0:
            raw["timeout_seconds"] = task.timeout_seconds
        if not task.supported:
            raw["supported"] = False
        if task.unsupported_reason is not None:
            raw["unsupported_reason"] = task.unsupported_reason
        if task.network_required:
            raw["network_required"] = True
        payload["tasks"].append(raw)
    return payload


def load_tasks(
    source: str | Path | Mapping[str, Any],
) -> list[AdaptiveBenchmarkTask]:
    """Compatibility loader returning only tasks from a versioned corpus."""

    return list(load_adaptive_corpus(source).tasks)


def _reject_csegraph_self_repository(value: str, *, context: str) -> None:
    normalized = value.replace("\\", "/").strip().strip("/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    lowered = normalized.lower()
    if lowered in {"", ".", "csegraph"} or lowered.startswith("csegraph/"):
        raise ValueError(
            f"{context} points at CseGraph itself; benchmark corpora must use sandbox "
            "or fixture repositories."
        )


def _parse_target_expectation(
    value: Any,
    task_id: str,
    field_name: str,
) -> BenchmarkTargetExpectation | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"Task {task_id!r} {field_name} must be an object")
    path = str(value.get("path") or "")
    line = value.get("line")
    if not path or isinstance(line, bool) or not isinstance(line, int) or line < 1:
        raise ValueError(f"Task {task_id!r} {field_name} requires a path and positive line")
    return BenchmarkTargetExpectation(
        path=path,
        line=line,
        name=str(value["name"]) if value.get("name") is not None else None,
        id=str(value["id"]) if value.get("id") is not None else None,
    )


def _parse_evidence_expectations(
    value: Any,
    task_id: str,
) -> tuple[BenchmarkEvidenceExpectation, ...]:
    if not isinstance(value, list):
        raise ValueError(f"Task {task_id!r} required_evidence must be an array")
    parsed: list[BenchmarkEvidenceExpectation] = []
    for position, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"Task {task_id!r} required_evidence[{position}] must be an object")
        path = str(item.get("path") or "")
        line = item.get("line")
        if not path or isinstance(line, bool) or not isinstance(line, int) or line < 1:
            raise ValueError(
                f"Task {task_id!r} required_evidence[{position}] requires a path and positive line"
            )
        parsed.append(
            BenchmarkEvidenceExpectation(
                path=path,
                line=line,
                role=str(item["role"]) if item.get("role") is not None else None,
            )
        )
    return tuple(parsed)


def _parse_permitted_ranges(
    value: Any,
    task_id: str,
) -> tuple[BenchmarkPermittedRange, ...]:
    if not isinstance(value, list):
        raise ValueError(f"Task {task_id!r} permitted_ranges must be an array")
    parsed: list[BenchmarkPermittedRange] = []
    for position, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"Task {task_id!r} permitted_ranges[{position}] must be an object")
        path = str(item.get("path") or "")
        lines = item.get("lines")
        if (
            not path
            or not isinstance(lines, list)
            or len(lines) != 2
            or any(isinstance(line, bool) or not isinstance(line, int) for line in lines)
            or lines[0] < 1
            or lines[1] < lines[0]
        ):
            raise ValueError(
                f"Task {task_id!r} permitted_ranges[{position}] requires path and [start, end]"
            )
        parsed.append(
            BenchmarkPermittedRange(
                path=path,
                start_line=lines[0],
                end_line=lines[1],
            )
        )
    return tuple(parsed)


def _parse_hidden_checks(value: Any, task_id: str) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        raise ValueError(f"Task {task_id!r} hidden_checks must be an array")
    parsed: list[tuple[str, ...]] = []
    for position, check in enumerate(value):
        if isinstance(check, str):
            argv = tuple(shlex.split(check))
        elif isinstance(check, list):
            argv = tuple(str(item) for item in check)
        else:
            raise ValueError(
                f"Task {task_id!r} hidden check {position} must be a string or argv array"
            )
        if not argv:
            raise ValueError(f"Task {task_id!r} hidden check {position} is empty")
        parsed.append(argv)
    return tuple(parsed)


def validate_corpus(corpus: AdaptiveBenchmarkCorpus) -> None:
    """Validate cross-record invariants after schema parsing."""

    if corpus.schema_version not in TASK_SCHEMA_VERSIONS:
        raise ValueError(f"Unsupported benchmark schema {corpus.schema_version!r}")
    seen_ids: set[str] = set()
    for task in corpus.tasks:
        if task.id in seen_ids:
            raise ValueError(f"Duplicate benchmark task id {task.id!r}")
        seen_ids.add(task.id)
        repository = corpus.repositories.get(task.repo)
        if repository is None:
            raise ValueError(f"Task {task.id!r} references unknown repository {task.repo!r}")
        if repository.commit != task.commit:
            raise ValueError(f"Task {task.id!r} commit differs from repository {task.repo!r}")


load_adaptive_corpus = load_corpus
load_adaptive_tasks = load_tasks

__all__ = [
    "TASK_CATEGORIES",
    "TASK_SCHEMA_VERSION_V1",
    "TASK_SCHEMA_VERSION_V2",
    "TASK_SCHEMA_VERSIONS",
    "corpus_to_payload",
    "load_adaptive_corpus",
    "load_adaptive_tasks",
    "load_corpus",
    "load_tasks",
    "validate_corpus",
]
