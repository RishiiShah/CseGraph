"""Tool-only reproducible baseline for adaptive-context comparisons.

The baseline intentionally models a capable text-search agent rather than a
full-repository read: ripgrep discovery, deterministic match ranking, bounded
80-line reads, and one-hop Python import following under the same token
estimator and budget as CseGraph.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import select
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence
from urllib.parse import unquote, urlparse

from csegraph._core.retrieval.token_budget import (
    count_payload_tokens,
    token_estimator,
    token_measurement,
    validate_token_budget,
)
from csegraph._core.text.query_tokenizer import query_tokenizer

BASELINE_SCHEMA_VERSION = "csegraph-strong-baseline-v2"
TASK_SCHEMA_VERSION_V1 = "csegraph-adaptive-benchmark-v1"
TASK_SCHEMA_VERSION_V2 = "csegraph-adaptive-benchmark-v2"
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
TASK_SCHEMA_VERSIONS = {TASK_SCHEMA_VERSION_V1, TASK_SCHEMA_VERSION_V2}
PINNED_PYRIGHT_VERSION = "1.1.407"
MAX_MATCHES = 5
# Three ranked hits model the common "rg ... | head" agent workflow while
# reserving most of an 800-token transcript for source rather than hit metadata.
MAX_DISCOVERY_MATCHES = 3
WINDOW_LINES = 80
MIN_WINDOW_LINES = 10
BENCHMARK_ARTIFACT_NAMES = frozenset(
    {
        ".DS_Store",
        ".cache",
        ".coverage",
        ".csegraph",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
    }
)
BENCHMARK_ARTIFACT_SUFFIXES = (".pyc", ".pyo")
LOCAL_COPY_URLS = frozenset({"fixture://local", "sandbox://local"})


def _baseline_intent(task: str, task_kind: str) -> str:
    normalized = task_kind.strip().lower().replace("_", "-")
    if normalized in {"cross-file", "edit", "refactor", "review", "test-impact"}:
        return "impact"
    if normalized in {"debug", "structural"}:
        return normalized
    if normalized in {"definition", "understand", "ambiguous"}:
        return "definition"

    tokens = set(query_tokenizer.tokenize(task))
    if tokens & {"debug", "error", "failed", "failing", "failure", "traceback"}:
        return "debug"
    if tokens & {
        "add",
        "change",
        "edit",
        "fix",
        "implement",
        "modify",
        "refactor",
        "remove",
        "rename",
        "replace",
        "update",
    }:
        return "impact"
    if tokens & {"architecture", "blast", "flow", "path", "structure"}:
        return "structural"
    return "definition"


class DefinitionProvider(Protocol):
    def definitions(
        self,
        repo: Path,
        path: Path,
        line: int,
        character: int,
    ) -> Sequence[tuple[Path, int]]:
        """Return definition locations using a pinned external LSP."""

    def references(
        self,
        repo: Path,
        path: Path,
        line: int,
        character: int,
    ) -> Sequence[tuple[Path, int]]:
        """Return reference locations using a pinned external LSP."""


class PyrightLspProvider:
    """Small persistent JSON-RPC client for a pinned Pyright language server.

    Pyright is deliberately optional. A missing binary, a version mismatch, or
    a protocol failure disables LSP enrichment and leaves the rg baseline
    usable. The benchmark report exposes the reason instead of silently
    claiming that LSP participated.
    """

    def __init__(
        self,
        *,
        command: Sequence[str] = ("pyright-langserver", "--stdio"),
        version_command: Sequence[str] = ("pyright", "--version"),
        expected_version: str = PINNED_PYRIGHT_VERSION,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.command = tuple(command)
        self.version_command = tuple(version_command)
        self.expected_version = expected_version
        self.timeout_seconds = timeout_seconds
        self.warning: str | None = None
        self.observed_version: str | None = None
        self.last_latency_ms = 0.0
        self._process: subprocess.Popen[bytes] | None = None
        self._buffer = bytearray()
        self._request_id = 0
        self._root: Path | None = None
        self._opened: set[Path] = set()
        self._available = self._check_available()

    @property
    def available(self) -> bool:
        return self._available

    def _check_available(self) -> bool:
        if not self.command or shutil.which(self.command[0]) is None:
            self.warning = f"Pyright LSP unavailable: {self.command[0]!r} was not found"
            return False
        if not self.version_command or shutil.which(self.version_command[0]) is None:
            self.warning = (
                "Pyright LSP disabled: the version probe command "
                f"{self.version_command[0]!r} was not found"
            )
            return False
        try:
            result = subprocess.run(
                self.version_command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.warning = f"Pyright LSP disabled: version probe failed ({exc})"
            return False
        version_text = f"{result.stdout}\n{result.stderr}"
        match = re.search(r"(\d+\.\d+\.\d+)", version_text)
        observed = match.group(1) if match else None
        self.observed_version = observed
        if result.returncode != 0 or observed != self.expected_version:
            self.warning = (
                "Pyright LSP disabled: expected version "
                f"{self.expected_version}, observed {observed or 'unknown'}"
            )
            return False
        return True

    def definitions(
        self,
        repo: Path,
        path: Path,
        line: int,
        character: int,
    ) -> Sequence[tuple[Path, int]]:
        return self._locations(
            repo,
            path,
            line,
            character,
            method="textDocument/definition",
        )

    def references(
        self,
        repo: Path,
        path: Path,
        line: int,
        character: int,
    ) -> Sequence[tuple[Path, int]]:
        return self._locations(
            repo,
            path,
            line,
            character,
            method="textDocument/references",
            extra={"context": {"includeDeclaration": False}},
        )

    def _locations(
        self,
        repo: Path,
        path: Path,
        line: int,
        character: int,
        *,
        method: str,
        extra: dict[str, Any] | None = None,
    ) -> list[tuple[Path, int]]:
        if not self._available:
            return []
        started = time.perf_counter()
        try:
            self._ensure_started(repo)
            self._open_document(path)
            params: dict[str, Any] = {
                "textDocument": {"uri": path.resolve().as_uri()},
                "position": {
                    "line": max(0, line - 1),
                    "character": max(0, character),
                },
            }
            if extra:
                params.update(extra)
            response = self._request(method, params)
            return _lsp_locations(response.get("result"))
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            self.warning = f"Pyright LSP disabled after protocol failure: {exc}"
            self._available = False
            self.close()
            return []
        finally:
            self.last_latency_ms = round((time.perf_counter() - started) * 1000, 3)

    def _ensure_started(self, repo: Path) -> None:
        repo = repo.resolve()
        if self._process is not None and self._root == repo:
            return
        self.close()
        self._process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._root = repo
        self._opened.clear()
        self._buffer.clear()
        self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": repo.as_uri(),
                "capabilities": {},
                "workspaceFolders": [{"uri": repo.as_uri(), "name": repo.name}],
            },
        )
        self._notify("initialized", {})

    def _open_document(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved in self._opened:
            return
        text = resolved.read_text(encoding="utf-8", errors="replace")
        self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": resolved.as_uri(),
                    "languageId": "python",
                    "version": 1,
                    "text": text,
                }
            },
        )
        self._opened.add(resolved)

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            message = self._read(deadline)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error']}")
                return message

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError("Pyright language server is not running")
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        process.stdin.flush()

    def _read(self, deadline: float) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdout is None:
            raise RuntimeError("Pyright language server is not running")
        while True:
            separator = self._buffer.find(b"\r\n\r\n")
            if separator >= 0:
                header = bytes(self._buffer[:separator]).decode("ascii", errors="replace")
                match = re.search(r"(?im)^Content-Length:\s*(\d+)\s*$", header)
                if match is None:
                    raise RuntimeError("Pyright returned an invalid JSON-RPC header")
                length = int(match.group(1))
                body_start = separator + 4
                body_end = body_start + length
                if len(self._buffer) >= body_end:
                    body = bytes(self._buffer[body_start:body_end])
                    del self._buffer[:body_end]
                    value = json.loads(body)
                    if not isinstance(value, dict):
                        raise RuntimeError("Pyright returned a non-object JSON-RPC message")
                    return value
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("timed out waiting for Pyright")
            ready, _, _ = select.select([process.stdout.fileno()], [], [], remaining)
            if not ready:
                raise RuntimeError("timed out waiting for Pyright")
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("Pyright exited before returning a response")
            self._buffer.extend(chunk)

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        self._root = None
        self._opened.clear()
        self._buffer.clear()

    def __enter__(self) -> PyrightLspProvider:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


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
    repositories: Mapping[str, BenchmarkRepository]
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


class StrongBaselineAdapter:
    def __init__(
        self,
        *,
        encoding: str = "o200k_base",
        definition_provider: DefinitionProvider | None = None,
    ) -> None:
        self.encoding = encoding
        self.definition_provider = definition_provider

    def retrieve(
        self,
        repo: str | Path,
        task: str,
        *,
        target: str | None = None,
        task_kind: str = "auto",
        token_budget: int = 800,
        temperature: str = "cold",
    ) -> BaselineResult:
        validate_token_budget(token_budget)
        started = time.perf_counter()
        repo_path = Path(repo).resolve()
        rg_started = time.perf_counter()
        matches = _rg_matches(repo_path, task, target)
        rg_latency_ms = (time.perf_counter() - rg_started) * 1000
        ranked = _rank_matches(matches, task, target)
        result = BaselineResult(
            schema_version=BASELINE_SCHEMA_VERSION,
            query=task,
            target=target,
            slices=[],
            usage={
                "tokens": 0,
                "budget": token_budget,
                "encoding": self.encoding,
                "estimator": token_estimator(self.encoding),
                "measurement": token_measurement(self.encoding),
                "latency_ms": 0.0,
                "tool_latency_ms": 0.0,
                "engine_latency_ms": 0.0,
                "external_tool_latency_ms": 0.0,
                "rg_latency_ms": round(rg_latency_ms, 3),
                "lsp_latency_ms": 0.0,
                "tool_calls": 1,
                "rg_calls": 1,
                "file_read_calls": 0,
                "lsp_calls": 0,
                "temperature": temperature,
            },
        )
        for match in ranked[:MAX_DISCOVERY_MATCHES]:
            result.discovery.append(
                {
                    "path": str(match["path"]),
                    "line": int(match["line"]),
                    "text": str(match["text"]).strip()[:240],
                }
            )
            if not _baseline_within_budget(result, self.encoding, token_budget):
                result.discovery.pop()
                break
        result.usage["discovery_matches"] = len(result.discovery)
        result.usage["discovery_truncated"] = len(result.discovery) < min(
            len(ranked), MAX_DISCOVERY_MATCHES
        )
        seen: set[tuple[str, int, int]] = set()
        selected_paths: list[Path] = []

        target_lower = (
            _target_search_terms(target)[0].lower()
            if target and _target_search_terms(target)
            else ""
        )

        def _is_lexical_definition(match: dict[str, Any]) -> bool:
            if not target_lower:
                return False
            return bool(
                re.search(
                    rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(target_lower)}\b",
                    str(match["text"]).lower(),
                )
            )

        lexical_defs = [m for m in ranked[:MAX_MATCHES] if _is_lexical_definition(m)]
        unique_lexical_match = lexical_defs[0] if len(lexical_defs) == 1 else None
        intent = _baseline_intent(task, task_kind)
        needs_impact_context = intent in {"impact", "debug", "structural"}
        selected_matches = (
            [unique_lexical_match]
            if unique_lexical_match is not None and not needs_impact_context
            else ranked[:MAX_MATCHES]
        )

        for rank, match in enumerate(selected_matches):
            path = repo_path / match["path"]
            accepted = _append_fitting_window(
                result,
                path=path,
                relative_path=str(match["path"]),
                line=int(match["line"]),
                role="match" if rank else "target",
                seen=seen,
                encoding=self.encoding,
                token_budget=token_budget,
            )
            if not accepted:
                continue
            selected_paths.append(path)

            if unique_lexical_match is not None and match == unique_lexical_match:
                break

        if needs_impact_context or unique_lexical_match is None:
            for import_path in _one_hop_python_imports(repo_path, selected_paths):
                rel = import_path.relative_to(repo_path).as_posix()
                if not _append_fitting_window(
                    result,
                    path=import_path,
                    relative_path=rel,
                    line=1,
                    role="import",
                    seen=seen,
                    encoding=self.encoding,
                    token_budget=token_budget,
                ):
                    break

        lsp_latency_ms = 0.0
        lsp_calls = 0
        if (
            self.definition_provider is not None
            and ranked
            and (unique_lexical_match is None or needs_impact_context)
        ):
            first = ranked[0]
            source = repo_path / first["path"]
            provider_locations: list[tuple[str, Path, int]] = []
            methods: list[tuple[str, str]] = []
            if unique_lexical_match is None:
                methods.append(("definitions", "definition"))
            if needs_impact_context:
                methods.append(("references", "reference"))
            for method, role in methods:
                provider_method = getattr(self.definition_provider, method, None)
                if provider_method is None:
                    continue
                lsp_calls += 1
                locations = provider_method(
                    repo_path,
                    source,
                    int(first["line"]),
                    int(first.get("character", 0)),
                )
                lsp_latency_ms += float(getattr(self.definition_provider, "last_latency_ms", 0.0))
                provider_locations.extend((role, path, line) for path, line in locations)
            for role, definition_path, definition_line in provider_locations:
                try:
                    rel = definition_path.resolve().relative_to(repo_path).as_posix()
                except ValueError:
                    continue
                if not _append_fitting_window(
                    result,
                    path=definition_path,
                    relative_path=rel,
                    line=definition_line,
                    role=role,
                    seen=seen,
                    encoding=self.encoding,
                    token_budget=token_budget,
                ):
                    break
            warning = getattr(self.definition_provider, "warning", None)
            if warning:
                result.warnings.append(str(warning))

        elapsed_ms = (time.perf_counter() - started) * 1000
        external_ms = rg_latency_ms + lsp_latency_ms
        result.usage["latency_ms"] = round(elapsed_ms, 3)
        result.usage["tool_latency_ms"] = round(elapsed_ms, 3)
        result.usage["external_tool_latency_ms"] = round(external_ms, 3)
        result.usage["engine_latency_ms"] = round(max(0.0, elapsed_ms - external_ms), 3)
        result.usage["lsp_latency_ms"] = round(lsp_latency_ms, 3)
        result.usage["file_read_calls"] = len(result.slices)
        result.usage["lsp_calls"] = lsp_calls
        result.usage["tool_calls"] = (
            int(result.usage["rg_calls"])
            + int(result.usage["file_read_calls"])
            + int(result.usage["lsp_calls"])
        )
        _enforce_baseline_budget(result, self.encoding, token_budget)
        return result


def load_adaptive_corpus(path: str | Path) -> AdaptiveBenchmarkCorpus:
    corpus_path = Path(path)
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
    return AdaptiveBenchmarkCorpus(
        path=corpus_path.resolve(),
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


def load_adaptive_tasks(path: str | Path) -> list[AdaptiveBenchmarkTask]:
    """Compatibility loader returning only tasks from a versioned corpus."""

    return list(load_adaptive_corpus(path).tasks)


def corpus_quality(corpus: AdaptiveBenchmarkCorpus) -> dict[str, Any]:
    """Return benchmark task-mix quality metrics, warnings, and enforceable gates."""

    tasks = list(corpus.tasks)
    task_count = len(tasks)
    category_counts = _counts(task.category for task in tasks)
    status_counts = _counts(task.expected_status for task in tasks)
    execution_mode_counts = _counts(task.execution_mode for task in tasks)
    explicit_target_count = sum(task.target is not None for task in tasks)
    targetless_count = task_count - explicit_target_count
    ambiguous_count = sum(
        task.category == "ambiguous" or task.expected_status == "ambiguous" for task in tasks
    )
    structural_followup_count = sum(
        task.category == "structural" and task.expected_next_tool is not None for task in tasks
    )
    agent_task_count = sum(task.execution_mode == "agent" for task in tasks)
    required_test_evidence_count = sum(
        any(evidence.path.startswith(("test/", "tests/")) for evidence in task.required_evidence)
        for task in tasks
    )
    insufficient_budget_count = sum(task.expected_status == "insufficient" for task in tasks)
    exact_target_ratio = explicit_target_count / task_count if task_count else 0.0

    gates = {
        "targetless_coverage": targetless_count > 0,
        "ambiguous_coverage": ambiguous_count > 0,
        "structural_followup_coverage": structural_followup_count > 0,
        "agent_task_coverage": agent_task_count > 0,
        "required_test_evidence": required_test_evidence_count > 0,
        "insufficient_budget_coverage": insufficient_budget_count > 0,
        "exact_target_ratio_at_most_90pct": exact_target_ratio <= 0.90,
    }
    warnings: list[str] = []
    if task_count and explicit_target_count == task_count:
        warnings.append("all_tasks_have_explicit_targets")
    elif exact_target_ratio > 0.85:
        warnings.append("explicit_target_ratio_high")
    if not gates["targetless_coverage"]:
        warnings.append("targetless_coverage_missing")
    if not gates["ambiguous_coverage"]:
        warnings.append("ambiguous_coverage_missing")
    if not gates["structural_followup_coverage"]:
        warnings.append("structural_followup_coverage_missing")
    if not gates["agent_task_coverage"]:
        warnings.append("agent_task_coverage_missing")
    if not gates["required_test_evidence"]:
        warnings.append("required_test_evidence_missing")
    if not gates["insufficient_budget_coverage"]:
        warnings.append("insufficient_budget_coverage_missing")

    # Agent execution coverage is contract-enforced for PR corpora, while the
    # existing larger corpora remain retrieval-quality focused. Task execution
    # itself remains opt-in.
    # The perf and broad corpora intentionally favor stable exact targets for
    # high-N latency averages while keeping ambiguity/insufficient/structural
    # coverage from the sandbox release seed; exact-target ratio is therefore a
    # warning, not a perf/broad-tier failure.
    enforced_gate_names = tuple(
        name
        for name in gates
        if not (
            (name == "agent_task_coverage" and corpus.tier != "pr")
            or (corpus.tier in {"perf", "broad"} and name == "exact_target_ratio_at_most_90pct")
        )
    )
    enforced = corpus.tier in {"pr", "release", "perf", "broad"}
    passed = not enforced or all(gates[name] for name in enforced_gate_names)
    return {
        "metrics": {
            "task_count": task_count,
            "category_counts": category_counts,
            "status_counts": status_counts,
            "execution_mode_counts": execution_mode_counts,
            "explicit_target_count": explicit_target_count,
            "targetless_count": targetless_count,
            "explicit_target_ratio": round(exact_target_ratio, 4),
            "ambiguous_count": ambiguous_count,
            "structural_followup_count": structural_followup_count,
            "agent_task_count": agent_task_count,
            "required_test_evidence_count": required_test_evidence_count,
            "insufficient_budget_count": insufficient_budget_count,
        },
        "warnings": warnings,
        "gates": gates,
        "enforced": enforced,
        "enforced_gate_names": enforced_gate_names,
        "passed": passed,
    }


def copy_benchmark_repository(source: Path, destination: Path) -> dict[str, Any]:
    """Copy a benchmark repository into scratch without runtime artifacts."""

    shutil.copytree(source, destination, ignore=_benchmark_copy_ignore)
    return benchmark_workspace_hygiene(destination)


def benchmark_workspace_hygiene(path: Path) -> dict[str, Any]:
    """Report whether a benchmark workspace contains known runtime artifacts."""

    artifacts = [
        candidate.relative_to(path).as_posix()
        for candidate in path.rglob("*")
        if _is_benchmark_artifact_path(candidate.relative_to(path))
    ]
    return {
        "clean": not artifacts,
        "artifact_paths": sorted(artifacts)[:50],
        "artifact_count": len(artifacts),
        "ignored_names": sorted(BENCHMARK_ARTIFACT_NAMES),
        "ignored_suffixes": list(BENCHMARK_ARTIFACT_SUFFIXES),
    }


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


def corpus_completeness(corpus: AdaptiveBenchmarkCorpus) -> dict[str, Any]:
    expected_counts = {"pr": 22, "nightly": 60, "release": 30, "perf": 220, "broad": 348}
    expected = expected_counts[corpus.tier]
    supported = [task for task in corpus.tasks if task.supported]
    invalid_tasks: list[str] = []
    for task in supported:
        if corpus.schema_version == TASK_SCHEMA_VERSION_V2:
            expected_result_present = (
                task.expected_target is not None
                if task.expected_status == "ready"
                else bool(task.expected_candidates)
                if task.expected_status == "ambiguous"
                else True
            )
            if (
                not expected_result_present
                or not task.permitted_ranges
                or (task.expected_status == "ready" and not task.required_evidence)
            ):
                invalid_tasks.append(task.id)
                continue
        elif not task.expected_locations or not task.permitted_files:
            invalid_tasks.append(task.id)
            continue
        if task.execution_mode == "agent" and (not task.test_command or not task.hidden_checks):
            invalid_tasks.append(task.id)
    repository_pins_complete = all(
        task.repo in corpus.repositories and corpus.repositories[task.repo].commit == task.commit
        for task in supported
    )
    gates = {
        "corpus_status_ready": corpus.status == "ready",
        "task_count_exact": len(corpus.tasks) == expected,
        "all_tasks_supported": len(supported) == len(corpus.tasks),
        "task_contracts_complete": not invalid_tasks,
        "repository_pins_complete": repository_pins_complete,
    }
    return {
        "tier": corpus.tier,
        "expected_task_count": expected,
        "task_count": len(corpus.tasks),
        "supported_task_count": len(supported),
        "invalid_task_ids": invalid_tasks,
        "gates": gates,
        "complete": all(gates.values()),
    }


def _counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _benchmark_copy_ignore(directory: str, names: Sequence[str]) -> set[str]:
    base = Path(directory)
    return {
        name
        for name in names
        if _is_benchmark_artifact_path((base / name).relative_to(base))
        or name in BENCHMARK_ARTIFACT_NAMES
        or name.endswith(BENCHMARK_ARTIFACT_SUFFIXES)
    }


def _is_benchmark_artifact_path(path: Path) -> bool:
    parts = path.parts
    return any(part in BENCHMARK_ARTIFACT_NAMES for part in parts) or path.name.endswith(
        BENCHMARK_ARTIFACT_SUFFIXES
    )


def prepare_benchmark_repository(
    repository: BenchmarkRepository,
    *,
    repo_root: Path,
    cache_root: Path,
    bootstrap_missing: bool,
) -> PreparedRepository:
    requested = (repo_root / repository.path).resolve()
    if requested.is_dir():
        observed = (
            _fixture_revision(requested)
            if repository.url == "fixture://local"
            else _git_commit(requested)
        )
        return PreparedRepository(
            path=requested,
            observed_commit=observed,
            commit_matches=observed == repository.commit,
            bootstrapped=False,
            reason=None if observed == repository.commit else "repository_commit_mismatch",
        )
    if not bootstrap_missing:
        return PreparedRepository(
            path=None,
            observed_commit=None,
            commit_matches=False,
            bootstrapped=False,
            reason="repository_missing; rerun with --bootstrap-missing",
        )

    destination = cache_root / _safe_repository_cache_name(repository)
    if not destination.is_dir():
        destination.parent.mkdir(parents=True, exist_ok=True)
        clone = subprocess.run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                repository.url,
                str(destination),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if clone.returncode != 0:
            return PreparedRepository(
                path=None,
                observed_commit=None,
                commit_matches=False,
                bootstrapped=False,
                reason=f"clone_failed: {clone.stderr.strip()[:300]}",
            )
    checkout = subprocess.run(
        ["git", "-C", str(destination), "checkout", "--detach", repository.commit],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if checkout.returncode != 0:
        fetch = subprocess.run(
            [
                "git",
                "-C",
                str(destination),
                "fetch",
                "--depth=1",
                "origin",
                repository.commit,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if fetch.returncode == 0:
            checkout = subprocess.run(
                ["git", "-C", str(destination), "checkout", "--detach", repository.commit],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
    observed = _git_commit(destination)
    return PreparedRepository(
        path=destination if checkout.returncode == 0 else None,
        observed_commit=observed,
        commit_matches=observed == repository.commit,
        bootstrapped=True,
        reason=(
            None
            if checkout.returncode == 0 and observed == repository.commit
            else f"checkout_failed: {checkout.stderr.strip()[:300]}"
        ),
    )


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


def _rg_matches(repo: Path, task: str, target: str | None) -> list[dict[str, Any]]:
    terms = _target_search_terms(target)
    terms.extend(
        token
        for token in query_tokenizer.tokenize(task)
        if len(token) > 2 and token not in {"code", "file", "task", "with"}
    )
    terms = list(dict.fromkeys(terms))[:12]
    if not terms:
        return []
    pattern = "|".join(re.escape(term) for term in terms)
    try:
        result = subprocess.run(
            [
                "rg",
                "--json",
                "--ignore-case",
                "--line-number",
                "--glob",
                "!*.min.js",
                "--glob",
                "!.csegraph/**",
                pattern,
                str(repo),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []
    matches: list[dict[str, Any]] = []
    for raw_line in result.stdout.splitlines():
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data") or {}
        path_text = (data.get("path") or {}).get("text") or ""
        try:
            rel = Path(path_text).resolve().relative_to(repo).as_posix()
        except ValueError:
            continue
        line_text = (data.get("lines") or {}).get("text") or ""
        submatches = data.get("submatches") or []
        matches.append(
            {
                "path": rel,
                "line": int(data.get("line_number") or 1),
                "text": line_text,
                "character": int((submatches[0] if submatches else {}).get("start") or 0),
            }
        )
    return matches


def _rank_matches(
    matches: Sequence[dict[str, Any]],
    task: str,
    target: str | None,
) -> list[dict[str, Any]]:
    task_lower = task.lower()
    target_terms = _target_search_terms(target)
    target_lower = target_terms[0].lower() if target_terms else ""
    target_path = _target_path_hint(target)
    tokens = set(query_tokenizer.tokenize(task))

    def score(match: dict[str, Any]) -> tuple[float, str, int]:
        text = str(match["text"]).lower()
        path = str(match["path"]).lower()
        value = 0.0
        if target_path and path == target_path:
            value += 250.0
        elif target_path and path.endswith(f"/{target_path}"):
            value += 180.0
        if target_lower and target_lower in text:
            value += 50.0
        if target_lower and re.search(rf"\b{re.escape(target_lower)}\b", text):
            value += 25.0
        if target_lower and re.search(
            rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(target_lower)}\b",
            text,
        ):
            value += 100.0
        if target_lower and target_lower in path:
            value += 30.0
        value += 8.0 * sum(term.lower() in text for term in target_terms[1:])
        if path in task_lower:
            value += 20.0
        value += 3.0 * len(tokens & set(query_tokenizer.tokenize(text)))
        if re.search(r"^\s*(?:async\s+)?(?:def|class|function|export)\b", text):
            value += 12.0
        if "/test" in f"/{path}" and not (tokens & {"test", "tests", "failing"}):
            value -= 8.0
        return (-value, path, int(match["line"]))

    return sorted(matches, key=score)


def _target_path_hint(target: str | None) -> str:
    if not target or "::" not in target:
        return ""
    for part in target.split("::"):
        normalized = part.strip().replace("\\", "/").lstrip("./")
        if "/" in normalized or normalized.endswith((".py", ".js", ".jsx", ".ts", ".tsx")):
            return normalized.lower()
    return ""


def _read_window(path: Path, line: int, window_lines: int) -> tuple[int, int, str] | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    half = max(1, window_lines // 2)
    start = max(1, line - half)
    end = min(len(lines), start + window_lines - 1)
    start = max(1, end - window_lines + 1)
    return start, end, "\n".join(lines[start - 1 : end])


def _one_hop_python_imports(repo: Path, paths: Sequence[Path]) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    pattern = re.compile(
        r"^\s*(?:from\s+([A-Za-z_][A-Za-z0-9_.]*)\s+import|import\s+([A-Za-z_][A-Za-z0-9_.]*))"
    )
    for path in paths:
        if path.suffix != ".py":
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:80]
        except OSError:
            continue
        for line in lines:
            match = pattern.match(line)
            if match is None:
                continue
            module = match.group(1) or match.group(2)
            candidate = repo / (module.replace(".", "/") + ".py")
            package = repo / module.replace(".", "/") / "__init__.py"
            resolved = candidate if candidate.is_file() else package
            if resolved.is_file() and resolved not in seen:
                seen.add(resolved)
                found.append(resolved)
    return found


def _baseline_payload(result: BaselineResult) -> dict[str, Any]:
    return {
        "schema_version": result.schema_version,
        "query": result.query,
        "target": result.target,
        "discovery": result.discovery,
        "slices": [
            {
                "path": item.path,
                "lines": item.lines,
                "role": item.role,
                "code": item.code,
            }
            for item in result.slices
        ],
        "usage": result.usage,
        **({"warnings": result.warnings} if result.warnings else {}),
    }


def _converge_baseline_tokens(result: BaselineResult, encoding: str) -> int:
    result.usage["tokens"] = 0
    for _ in range(8):
        tokens = count_payload_tokens(_baseline_payload(result), encoding)
        if result.usage["tokens"] == tokens:
            return tokens
        result.usage["tokens"] = tokens
    return int(result.usage["tokens"])


def _baseline_within_budget(
    result: BaselineResult,
    encoding: str,
    token_budget: int,
) -> bool:
    return _converge_baseline_tokens(result, encoding) <= token_budget


def _enforce_baseline_budget(
    result: BaselineResult,
    encoding: str,
    token_budget: int,
) -> None:
    """Fit final latency/tool metadata into the same whole-response budget.

    Candidate construction budgets the envelope visible at selection time.
    Final latency fields and an optional LSP warning are added afterwards, so
    the completed response needs one last whole-payload pass. Lowest-ranked
    discovery evidence is removed first, followed by optional warnings and
    non-target slices. The primary target slice is never silently discarded.
    """
    while _converge_baseline_tokens(result, encoding) > token_budget:
        if result.discovery:
            result.discovery.pop()
            continue
        if result.warnings:
            result.warnings.pop()
            continue
        removable = next(
            (
                position
                for position in range(len(result.slices) - 1, -1, -1)
                if result.slices[position].role != "target"
            ),
            None,
        )
        if removable is not None:
            result.slices.pop(removable)
            continue
        break


def _append_fitting_window(
    result: BaselineResult,
    *,
    path: Path,
    relative_path: str,
    line: int,
    role: str,
    seen: set[tuple[str, int, int]],
    encoding: str,
    token_budget: int,
) -> bool:
    """Append the largest bounded window that keeps the whole transcript in budget."""

    for window_lines in (WINDOW_LINES, 60, 40, 20, MIN_WINDOW_LINES):
        window = _read_window(path, line, window_lines)
        if window is None:
            return False
        start, end, code = window
        key = (relative_path, start, end)
        if key in seen:
            return False
        candidate = BaselineSlice(
            path=relative_path,
            lines=[start, end],
            role=role,
            code=code,
        )
        result.slices.append(candidate)
        if _baseline_within_budget(result, encoding, token_budget):
            seen.add(key)
            return True
        result.slices.pop()
    _converge_baseline_tokens(result, encoding)
    return False


def _target_search_terms(target: str | None) -> list[str]:
    if not target:
        return []
    ignored = {
        "symbol",
        "function",
        "method",
        "class",
        "module",
        "property",
        "variable",
        "constant",
        "py",
        "js",
        "ts",
        "tsx",
        "jsx",
    }
    identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", target)
    useful = [item for item in identifiers if item.lower() not in ignored]
    # Qualified symbol IDs put the most discriminating identifiers last.
    useful.reverse()
    return list(dict.fromkeys(useful))[:5]


def _lsp_locations(value: Any) -> list[tuple[Path, int]]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    locations: list[tuple[Path, int]] = []
    seen: set[tuple[Path, int]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri") or item.get("targetUri")
        range_value = item.get("range") or item.get("targetSelectionRange")
        if not isinstance(uri, str) or not isinstance(range_value, dict):
            continue
        start = range_value.get("start")
        if not isinstance(start, dict):
            continue
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            continue
        path = Path(unquote(parsed.path))
        line = int(start.get("line", 0)) + 1
        key = (path, line)
        if key not in seen:
            seen.add(key)
            locations.append(key)
    return locations


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


def _safe_repository_cache_name(repository: BenchmarkRepository) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", repository.path).strip("-")
    return f"{slug}-{repository.commit[:12]}"


def _git_commit(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _fixture_revision(repo: Path) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    for path in sorted(
        (
            candidate
            for candidate in repo.rglob("*")
            if candidate.is_file() and not _is_benchmark_artifact_path(candidate.relative_to(repo))
        ),
        key=lambda candidate: candidate.relative_to(repo).as_posix(),
    ):
        relative = path.relative_to(repo).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


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
