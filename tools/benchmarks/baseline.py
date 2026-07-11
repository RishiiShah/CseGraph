"""Tool-only reproducible baseline for adaptive-context comparisons.

The baseline intentionally models a capable text-search agent rather than a
full-repository read: ripgrep discovery, deterministic match ranking, bounded
80-line reads, and one-hop Python import following under the same token
estimator and budget as CseGraph.
"""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Protocol, Sequence
from urllib.parse import unquote, urlparse

from csegraph._core.retrieval.token_budget import (
    count_payload_tokens,
    token_estimator,
    token_measurement,
    validate_token_budget,
)
from csegraph._core.text.query_tokenizer import query_tokenizer
from tools.benchmarks.models import (
    BaselineResult,
    BaselineSlice,
)

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
