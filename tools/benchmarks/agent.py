"""Deterministic tool-using repository agent for benchmark comparisons.

The agent deliberately receives only the user request. Expected targets and
evidence are evaluation metadata and never participate in query planning.
Repository profiles provide the local search shape and scenario budgets so the
trace reflects how an agent would explore different repositories.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from csegraph._core.retrieval.token_budget import (
    count_payload_tokens,
    token_estimator,
    token_measurement,
    validate_token_budget,
)
from csegraph._core.text.query_tokenizer import query_tokenizer
from tools.benchmarks.models import BaselineResult, BaselineSlice

BASELINE_SCHEMA_VERSION = "csegraph-strong-baseline-v3"


class DefinitionProvider(Protocol):
    warning: str | None
    last_latency_ms: float

    def definitions(
        self, repo: Path, path: Path, line: int, character: int
    ) -> Sequence[tuple[Path, int]]: ...

    def references(
        self, repo: Path, path: Path, line: int, character: int
    ) -> Sequence[tuple[Path, int]]: ...


@dataclass(frozen=True)
class AgentScenarioPolicy:
    """Task-shape controls supplied by a repository's benchmark profile."""

    category: str
    search_phases: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepositoryAgentProfile:
    """Repository-specific affordances and budgets for the simulated agent."""

    name: str
    source_globs: tuple[str, ...]
    query_budgets: Mapping[str, int]
    read_budgets: Mapping[str, int]
    read_windows: tuple[int, ...]
    match_budgets: Mapping[str, int] | None = None
    search_roots: tuple[str, ...] = ()
    test_roots: tuple[str, ...] = ()
    language: str = "python"
    search_phases: Mapping[str, tuple[str, ...]] | None = None

    def query_budget(self, policy: AgentScenarioPolicy) -> int:
        return _budget_for(self.query_budgets, policy.category)

    def read_budget(self, policy: AgentScenarioPolicy) -> int:
        return _budget_for(self.read_budgets, policy.category)

    def match_budget(self, policy: AgentScenarioPolicy) -> int:
        if self.match_budgets and policy.category in self.match_budgets:
            return max(1, int(self.match_budgets[policy.category]))
        return max(1, self.query_budget(policy) * 8)

    def phases_for(self, policy: AgentScenarioPolicy) -> tuple[str, ...]:
        if self.search_phases and policy.category in self.search_phases:
            return self.search_phases[policy.category]
        if policy.search_phases:
            return policy.search_phases
        return _default_phases(policy.category)


def _budget_for(values: Mapping[str, int], category: str) -> int:
    if category in values:
        return max(1, int(values[category]))
    if "default" in values:
        return max(1, int(values["default"]))
    return max(1, max((int(value) for value in values.values()), default=1))


def profile_for_repository(repo: Path, repo_key: str | None = None) -> RepositoryAgentProfile:
    """Infer a conservative local profile for fixtures or an unregistered repo."""

    key = (repo_key or repo.name).lower()
    if key.startswith("sandbox/"):
        from tools.benchmarks.sandbox import SANDBOX_REPOSITORIES

        for spec in SANDBOX_REPOSITORIES:
            if spec.path.lower() == key:
                return spec.agent_profile()
    if "adaptive_js_ts" in key:
        return RepositoryAgentProfile(
            name="fixture-js-ts",
            source_globs=("*.js", "*.ts", "*.tsx", "*.jsx"),
            search_roots=("src", "tests"),
            test_roots=("tests",),
            query_budgets={
                "definition": 2,
                "ambiguous": 3,
                "cross-file": 4,
                "debug": 4,
                "refactor": 5,
                "structural": 5,
                "test-impact": 5,
            },
            read_budgets={
                "definition": 2,
                "ambiguous": 4,
                "cross-file": 6,
                "debug": 6,
                "refactor": 8,
                "structural": 8,
                "test-impact": 8,
            },
            read_windows=(24, 48, 96),
            language="javascript-typescript",
        )
    if "adaptive_pr" in key:
        return RepositoryAgentProfile(
            name="fixture-python",
            source_globs=("*.py",),
            search_roots=("src", "tests"),
            test_roots=("tests",),
            query_budgets={
                "definition": 2,
                "ambiguous": 3,
                "cross-file": 4,
                "debug": 4,
                "refactor": 5,
                "structural": 5,
                "test-impact": 5,
            },
            read_budgets={
                "definition": 2,
                "ambiguous": 4,
                "cross-file": 6,
                "debug": 6,
                "refactor": 8,
                "structural": 8,
                "test-impact": 8,
            },
            read_windows=(24, 48, 96),
        )

    file_count = sum(1 for path in repo.rglob("*") if path.is_file())
    if file_count < 40:
        scale = (2, 3, 4, 5, 6)
    elif file_count < 400:
        scale = (3, 4, 6, 7, 9)
    else:
        scale = (4, 6, 8, 10, 12)
    source_globs = _detected_source_globs(repo)
    return RepositoryAgentProfile(
        name=f"inferred-{file_count}",
        source_globs=source_globs,
        search_roots=(),
        test_roots=(),
        query_budgets=dict(
            zip(
                ("definition", "ambiguous", "cross-file", "debug", "refactor"),
                scale,
                strict=True,
            )
        )
        | {"structural": scale[4], "test-impact": scale[4]},
        read_budgets={
            "definition": scale[0],
            "ambiguous": scale[1],
            "cross-file": scale[2],
            "debug": scale[2],
            "refactor": scale[3],
            "structural": scale[4],
            "test-impact": scale[4],
        },
        read_windows=(24, 48, 96, 160),
    )


def _detected_source_globs(repo: Path) -> tuple[str, ...]:
    suffixes = {path.suffix for path in repo.rglob("*") if path.is_file()}
    preferred = tuple(
        f"*{suffix}" for suffix in (".py", ".js", ".ts", ".tsx", ".jsx") if suffix in suffixes
    )
    return preferred or ("*.py",)


class RepositoryAgent:
    """Run a reproducible search/read loop against a repository."""

    def __init__(self, *, encoding: str = "o200k_base") -> None:
        self.encoding = encoding

    def retrieve(
        self,
        repo: str | Path,
        request: str,
        *,
        task_kind: str = "auto",
        visible_target: str | None = None,
        profile: RepositoryAgentProfile | None = None,
        policy: AgentScenarioPolicy | None = None,
        token_budget: int = 800,
        temperature: str = "cold",
        definition_provider: Any | None = None,
    ) -> BaselineResult:
        validate_token_budget(token_budget)
        started = time.perf_counter()
        repo_path = Path(repo).resolve()
        category = _infer_task_kind(request, task_kind)
        profile = profile or profile_for_repository(repo_path)
        policy = policy or AgentScenarioPolicy(category=category)
        trace: list[dict[str, Any]] = []
        matches: dict[tuple[str, int], dict[str, Any]] = {}
        result = BaselineResult(
            schema_version=BASELINE_SCHEMA_VERSION,
            query=request,
            target=None,
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
                "rg_latency_ms": 0.0,
                "lsp_latency_ms": 0.0,
                "tool_calls": 0,
                "rg_calls": 0,
                "query_count": 0,
                "file_read_calls": 0,
                "lsp_calls": 0,
                "temperature": temperature,
                "profile": profile.name,
                "policy": category,
                "trace": trace,
            },
        )

        inventory_started = time.perf_counter()
        inventory = _inventory(repo_path, profile)
        inventory_latency = (time.perf_counter() - inventory_started) * 1000
        trace.append(
            {
                "action": "inventory",
                "path_count": len(inventory),
                "latency_ms": round(inventory_latency, 3),
            }
        )
        result.usage["tool_calls"] = 1
        result.usage["rg_calls"] = 1
        result.usage["inventory_count"] = len(inventory)

        query_budget = profile.query_budget(policy)
        read_budget = profile.read_budget(policy)
        phases = profile.phases_for(policy)
        query_plan = _query_plan(request, category, phases, visible_target=visible_target)
        read_paths: set[str] = set()
        selected_paths: list[Path] = []
        seen_windows: set[tuple[str, int, int]] = set()
        ranked: list[dict[str, Any]] = []
        stop_reason = "query_budget_exhausted"
        external_latency = inventory_latency
        lsp_latency = 0.0

        for phase, query, roots in query_plan:
            if result.usage["query_count"] >= query_budget:
                break
            search_started = time.perf_counter()
            found = _rg_matches(
                repo_path,
                query,
                roots=roots,
                globs=profile.source_globs,
                result_limit=profile.match_budget(policy),
            )
            search_latency = (time.perf_counter() - search_started) * 1000
            external_latency += search_latency
            result.usage["query_count"] += 1
            result.usage["rg_calls"] += 1
            result.usage["tool_calls"] += 1
            trace.append(
                {
                    "action": "search",
                    "phase": phase,
                    "query": query,
                    "roots": list(roots),
                    "match_count": len(found),
                    "result_limit": profile.match_budget(policy),
                    "latency_ms": round(search_latency, 3),
                }
            )
            for match in found:
                matches[(str(match["path"]), int(match["line"]))] = match
            ranked = _rank_matches(tuple(matches.values()), request)

            for match in ranked:
                if len(read_paths) >= read_budget:
                    break
                path_text = str(match["path"])
                line = int(match["line"])
                if path_text in read_paths:
                    continue
                path = repo_path / path_text
                role = "target" if not result.slices else _role_for(category, path_text)
                if not _append_fitting_window(
                    result,
                    path=path,
                    relative_path=path_text,
                    line=line,
                    role=role,
                    seen=seen_windows,
                    windows=profile.read_windows,
                    token_budget=token_budget,
                    trace=trace,
                ):
                    continue
                read_paths.add(path_text)
                selected_paths.append(path)
                result.usage["file_read_calls"] += 1
                result.usage["tool_calls"] += 1

            if ranked:
                _append_discovery(result, ranked[0], token_budget, self.encoding)
            if _is_sufficient(category, ranked, result.slices):
                stop_reason = "evidence_sufficient"
                break

        if category in {"cross-file", "debug", "refactor", "structural", "test-impact"}:
            for import_path in _one_hop_python_imports(repo_path, selected_paths):
                if len(read_paths) >= read_budget:
                    break
                relative = import_path.relative_to(repo_path).as_posix()
                if relative in read_paths:
                    continue
                if _append_fitting_window(
                    result,
                    path=import_path,
                    relative_path=relative,
                    line=1,
                    role="import",
                    seen=seen_windows,
                    windows=profile.read_windows,
                    token_budget=token_budget,
                    trace=trace,
                ):
                    read_paths.add(relative)
                    result.usage["file_read_calls"] += 1
                    result.usage["tool_calls"] += 1

        if definition_provider is not None and ranked:
            first = ranked[0]
            source = repo_path / str(first["path"])
            methods: list[tuple[str, str]] = []
            if not _has_lexical_definition(ranked):
                methods.append(("definitions", "definition"))
            if category in {"cross-file", "debug", "refactor", "structural", "test-impact"}:
                methods.append(("references", "reference"))
            for method, role in methods:
                provider_method = getattr(definition_provider, method, None)
                if provider_method is None:
                    continue
                locations = provider_method(
                    repo_path,
                    source,
                    int(first["line"]),
                    int(first.get("character", 0)),
                )
                result.usage["lsp_calls"] += 1
                result.usage["tool_calls"] += 1
                provider_latency = float(getattr(definition_provider, "last_latency_ms", 0.0))
                lsp_latency += provider_latency
                external_latency += provider_latency
                for location, line in locations:
                    try:
                        relative = location.resolve().relative_to(repo_path).as_posix()
                    except ValueError:
                        continue
                    if len(read_paths) >= read_budget:
                        break
                    if relative in read_paths:
                        continue
                    if _append_fitting_window(
                        result,
                        path=location,
                        relative_path=relative,
                        line=line,
                        role=role,
                        seen=seen_windows,
                        windows=profile.read_windows,
                        token_budget=token_budget,
                        trace=trace,
                    ):
                        read_paths.add(relative)
                        result.usage["file_read_calls"] += 1
                        result.usage["tool_calls"] += 1
            warning = getattr(definition_provider, "warning", None)
            if warning:
                result.warnings.append(str(warning))

        result.usage["stop_reason"] = stop_reason
        result.usage["external_tool_latency_ms"] = round(external_latency, 3)
        result.usage["lsp_latency_ms"] = round(lsp_latency, 3)
        elapsed_ms = (time.perf_counter() - started) * 1000
        result.usage["latency_ms"] = round(elapsed_ms, 3)
        result.usage["tool_latency_ms"] = round(elapsed_ms, 3)
        result.usage["engine_latency_ms"] = round(max(0.0, elapsed_ms - external_latency), 3)
        result.usage["rg_latency_ms"] = round(
            sum(
                float(event.get("latency_ms", 0.0))
                for event in trace
                if event["action"] in {"inventory", "search"}
            ),
            3,
        )
        result.usage["trace"] = trace
        _enforce_budget(result, self.encoding, token_budget)
        return result


def _infer_task_kind(request: str, task_kind: str) -> str:
    normalized = task_kind.strip().lower().replace("_", "-")
    if normalized != "auto":
        if normalized in {"understand", "definition"}:
            return "definition"
        if normalized in {"edit", "impact", "cross-file"}:
            return "cross-file"
        return normalized
    tokens = set(query_tokenizer.tokenize(request))
    if tokens & {"test", "tests", "failing", "failure", "debug", "error"}:
        return "test-impact"
    if tokens & {"update", "change", "implement", "modify", "refactor", "fix", "callers"}:
        return "cross-file"
    if tokens & {"architecture", "flow", "trace", "structure"}:
        return "structural"
    return "definition"


def _default_phases(category: str) -> tuple[str, ...]:
    return {
        "definition": ("identifier", "path"),
        "ambiguous": ("identifier", "path", "disambiguation"),
        "cross-file": ("identifier", "callers", "imports", "tests"),
        "debug": ("identifier", "error", "tests", "callers"),
        "refactor": ("identifier", "callers", "imports", "tests"),
        "structural": ("identifier", "callers", "imports", "tests"),
        "test-impact": ("identifier", "tests", "callers", "imports"),
    }.get(category, ("identifier", "path"))


def _query_plan(
    request: str,
    category: str,
    phases: Sequence[str],
    *,
    visible_target: str | None = None,
) -> list[tuple[str, str, tuple[str, ...]]]:
    words = _request_terms(request)
    if visible_target:
        words = list(dict.fromkeys(_visible_target_terms(visible_target) + words))
    if not words:
        words = [request.strip()]
    plan: list[tuple[str, str, tuple[str, ...]]] = []
    for phase in phases:
        if phase == "identifier":
            plan.extend((phase, word, ()) for word in words)
        elif phase == "path":
            for word in words:
                if "." in word or "/" in word or "_" in word:
                    plan.append((phase, word.replace(".", "/"), ()))
        elif phase in {"callers", "imports", "tests", "error", "disambiguation"}:
            for word in words[:2]:
                plan.append((phase, word, ("tests",) if phase == "tests" else ()))
    if category == "definition" and not plan:
        plan.append(("identifier", request, ()))
    return plan


_STOPWORDS = {
    "a",
    "an",
    "and",
    "around",
    "architecture",
    "complete",
    "describe",
    "explain",
    "find",
    "function",
    "implementation",
    "inspect",
    "the",
    "this",
    "trace",
    "update",
    "with",
}


def _request_terms(request: str) -> list[str]:
    terms = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", request)
    useful = [term for term in terms if term.lower() not in _STOPWORDS]
    return list(dict.fromkeys(useful))


def _visible_target_terms(target: str) -> list[str]:
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
    terms = [
        term
        for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", target)
        if term.lower() not in ignored
    ]
    return list(dict.fromkeys(reversed(terms)))


def _inventory(repo: Path, profile: RepositoryAgentProfile) -> list[str]:
    command = ["rg", "--files", "--hidden", "--glob", "!.git/**", "--glob", "!.csegraph/**"]
    for glob in profile.source_globs:
        command.extend(("--glob", glob))
    command.append(str(repo))
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _rg_matches(
    repo: Path,
    query: str,
    *,
    roots: Sequence[str],
    globs: Sequence[str],
    result_limit: int,
) -> list[dict[str, Any]]:
    command = [
        "rg",
        "--json",
        "--ignore-case",
        "--line-number",
        "--glob",
        "!.git/**",
        "--glob",
        "!.csegraph/**",
    ]
    for glob in globs:
        command.extend(("--glob", glob))
    command.extend(("--", query, *(roots or (str(repo),))))
    try:
        process = subprocess.Popen(
            command,
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return []
    matches: list[dict[str, Any]] = []
    try:
        stdout = process.stdout
        if stdout is None:
            return []
        for raw_line in stdout:
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event.get("data") or {}
            path_text = (data.get("path") or {}).get("text") or ""
            relative = Path(path_text).as_posix()
            if relative.startswith(f"{repo.as_posix()}/"):
                relative = relative[len(repo.as_posix()) + 1 :]
            line_text = (data.get("lines") or {}).get("text") or ""
            submatches = data.get("submatches") or []
            matches.append(
                {
                    "path": relative,
                    "line": int(data.get("line_number") or 1),
                    "text": line_text,
                    "character": int((submatches[0] if submatches else {}).get("start") or 0),
                }
            )
            if len(matches) >= result_limit:
                process.terminate()
                break
    except (OSError, ValueError):
        process.terminate()
    finally:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    return matches


def _rank_matches(matches: Sequence[dict[str, Any]], request: str) -> list[dict[str, Any]]:
    tokens = set(item.lower() for item in _request_terms(request))

    def score(match: dict[str, Any]) -> tuple[float, str, int]:
        text = str(match["text"]).lower()
        path = str(match["path"]).lower()
        value = 4.0 * len(tokens & set(query_tokenizer.tokenize(text)))
        value += 3.0 * len(tokens & set(query_tokenizer.tokenize(path)))
        if re.search(r"^\s*(?:async\s+)?(?:def|class|function|export)\b", text):
            value += 12.0
        if "/test" in f"/{path}" and not {"test", "tests"} & tokens:
            value -= 8.0
        return (-value, path, int(match["line"]))

    return sorted(matches, key=score)


def _role_for(category: str, path: str) -> str:
    if "/test" in f"/{path}" or path.startswith("test"):
        return "test" if category in {"debug", "test-impact"} else "evidence"
    if category in {"cross-file", "refactor", "structural"}:
        return "impact"
    return "match"


def _is_sufficient(
    category: str,
    ranked: Sequence[dict[str, Any]],
    slices: Sequence[BaselineSlice],
) -> bool:
    if not ranked or not slices:
        return False
    if category == "definition":
        return _has_lexical_definition(ranked)
    if category in {"ambiguous", "structural", "cross-file", "debug", "refactor", "test-impact"}:
        return len(slices) >= 2
    return True


def _has_lexical_definition(matches: Sequence[dict[str, Any]]) -> bool:
    return any(
        re.search(r"^\s*(?:async\s+)?(?:def|class|function|export)\b", str(match["text"]))
        for match in matches
    )


def _read_window(path: Path, line: int, window_lines: int) -> tuple[int, int, str] | None:
    half = max(1, window_lines // 2)
    start = max(1, line - half)
    end = line + half
    lines: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for number, source_line in enumerate(handle, 1):
                if number > end:
                    break
                if number >= start:
                    lines.append(source_line.rstrip("\r\n"))
    except OSError:
        return None
    if not lines:
        return None
    actual_end = start + len(lines) - 1
    return start, actual_end, "\n".join(lines)


def _append_fitting_window(
    result: BaselineResult,
    *,
    path: Path,
    relative_path: str,
    line: int,
    role: str,
    seen: set[tuple[str, int, int]],
    windows: Sequence[int],
    token_budget: int,
    trace: list[dict[str, Any]],
) -> bool:
    for window_lines in sorted(set(windows), reverse=True):
        window = _read_window(path, line, window_lines)
        if window is None:
            return False
        start, end, code = window
        key = (relative_path, start, end)
        if key in seen:
            return False
        result.slices.append(
            BaselineSlice(path=relative_path, lines=[start, end], role=role, code=code)
        )
        if _within_budget(result, token_budget):
            seen.add(key)
            trace.append(
                {
                    "action": "read",
                    "path": relative_path,
                    "lines": [start, end],
                    "role": role,
                }
            )
            return True
        result.slices.pop()
    return False


def _append_discovery(
    result: BaselineResult,
    match: Mapping[str, Any],
    token_budget: int,
    encoding: str,
) -> None:
    result.discovery.append(
        {
            "path": str(match["path"]),
            "line": int(match["line"]),
            "text": str(match["text"]).strip()[:240],
        }
    )
    if not _within_budget(result, token_budget):
        result.discovery.pop()


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
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:120]
        except OSError:
            continue
        for source_line in lines:
            match = pattern.match(source_line)
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


def _content_payload(result: BaselineResult) -> dict[str, Any]:
    usage = {key: value for key, value in result.usage.items() if key != "trace"}
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
        "usage": usage,
    }


def _within_budget(result: BaselineResult, token_budget: int) -> bool:
    payload = _content_payload(result)
    code_chars = sum(len(item["code"]) for item in payload["slices"])
    discovery_chars = sum(len(str(item)) for item in payload["discovery"])
    metadata_chars = len(json.dumps({"query": payload["query"], "usage": payload["usage"]}))
    estimated_tokens = max(1, int((code_chars + discovery_chars + metadata_chars) / 3.2))
    return estimated_tokens <= token_budget


def _enforce_budget(result: BaselineResult, encoding: str, token_budget: int) -> None:
    while True:
        result.usage["tokens"] = count_payload_tokens(_content_payload(result), encoding)
        if int(result.usage["tokens"]) <= token_budget:
            return
        if result.discovery:
            result.discovery.pop()
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


__all__ = [
    "AgentScenarioPolicy",
    "RepositoryAgent",
    "RepositoryAgentProfile",
    "profile_for_repository",
]
