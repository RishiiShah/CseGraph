"""Strong, reproducible retrieval baseline for adaptive-context comparisons.

The baseline intentionally models a capable text-search agent rather than a
full-repository read: ripgrep discovery, deterministic match ranking, bounded
80-line reads, and one-hop Python import following under the same exact token
budget as CseGraph.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from csegraph._core.retrieval.token_budget import (
    count_payload_tokens,
    validate_token_budget,
)
from csegraph._core.text.query_tokenizer import query_tokenizer

BASELINE_SCHEMA_VERSION = "csegraph-strong-baseline-v1"
TASK_SCHEMA_VERSION = "csegraph-adaptive-benchmark-v1"
MAX_MATCHES = 5
WINDOW_LINES = 80


class DefinitionProvider(Protocol):
    def definitions(
        self,
        repo: Path,
        path: Path,
        line: int,
        character: int,
    ) -> Sequence[tuple[Path, int]]:
        """Return definition locations using a pinned external LSP."""


@dataclass(frozen=True)
class AdaptiveBenchmarkTask:
    id: str
    repo: str
    commit: str
    category: str
    task: str
    target: str | None = None
    expected_locations: tuple[str, ...] = ()
    permitted_files: tuple[str, ...] = ()
    setup_command: tuple[str, ...] = ()
    test_command: tuple[str, ...] = ()
    hidden_checks: tuple[str, ...] = ()


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
        token_budget: int = 800,
    ) -> BaselineResult:
        validate_token_budget(token_budget)
        started = time.perf_counter()
        repo_path = Path(repo).resolve()
        matches = _rg_matches(repo_path, task, target)
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
                "latency_ms": 0.0,
                "tool_calls": 1,
            },
        )
        seen: set[tuple[str, int, int]] = set()
        selected_paths: list[Path] = []
        for rank, match in enumerate(ranked[:MAX_MATCHES]):
            path = repo_path / match["path"]
            window = _read_window(path, int(match["line"]), WINDOW_LINES)
            if window is None:
                continue
            start, end, code = window
            key = (match["path"], start, end)
            if key in seen:
                continue
            seen.add(key)
            candidate = BaselineSlice(
                path=match["path"],
                lines=[start, end],
                role="match" if rank else "target",
                code=code,
            )
            result.slices.append(candidate)
            if not _baseline_within_budget(result, self.encoding, token_budget):
                result.slices.pop()
                continue
            selected_paths.append(path)

        for import_path in _one_hop_python_imports(repo_path, selected_paths):
            window = _read_window(import_path, 1, WINDOW_LINES)
            if window is None:
                continue
            start, end, code = window
            rel = import_path.relative_to(repo_path).as_posix()
            key = (rel, start, end)
            if key in seen:
                continue
            candidate = BaselineSlice(
                path=rel,
                lines=[start, end],
                role="import",
                code=code,
            )
            result.slices.append(candidate)
            if not _baseline_within_budget(result, self.encoding, token_budget):
                result.slices.pop()
                break
            seen.add(key)

        if self.definition_provider is not None and ranked:
            first = ranked[0]
            source = repo_path / first["path"]
            for definition_path, definition_line in self.definition_provider.definitions(
                repo_path,
                source,
                int(first["line"]),
                int(first.get("character", 0)),
            ):
                try:
                    rel = definition_path.resolve().relative_to(repo_path).as_posix()
                except ValueError:
                    continue
                window = _read_window(definition_path, definition_line, WINDOW_LINES)
                if window is None:
                    continue
                start, end, code = window
                key = (rel, start, end)
                if key in seen:
                    continue
                candidate = BaselineSlice(
                    path=rel,
                    lines=[start, end],
                    role="definition",
                    code=code,
                )
                result.slices.append(candidate)
                if not _baseline_within_budget(result, self.encoding, token_budget):
                    result.slices.pop()
                    break
                seen.add(key)

        result.usage["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        _converge_baseline_tokens(result, self.encoding)
        return result


def load_adaptive_tasks(path: str | Path) -> list[AdaptiveBenchmarkTask]:
    corpus_path = Path(path)
    payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != TASK_SCHEMA_VERSION:
        raise ValueError(
            f"Expected benchmark schema {TASK_SCHEMA_VERSION!r}, "
            f"got {payload.get('schema_version')!r}"
        )
    tasks: list[AdaptiveBenchmarkTask] = []
    required = {"id", "repo", "commit", "category", "task"}
    for position, raw in enumerate(payload.get("tasks") or []):
        if not isinstance(raw, dict):
            raise ValueError(f"Task at index {position} must be an object")
        missing = required - raw.keys()
        if missing:
            raise ValueError(f"Task {raw.get('id', position)!r} is missing {sorted(missing)}")
        tasks.append(
            AdaptiveBenchmarkTask(
                id=str(raw["id"]),
                repo=str(raw["repo"]),
                commit=str(raw["commit"]),
                category=str(raw["category"]),
                task=str(raw["task"]),
                target=str(raw["target"]) if raw.get("target") is not None else None,
                expected_locations=tuple(str(v) for v in raw.get("expected_locations", [])),
                permitted_files=tuple(str(v) for v in raw.get("permitted_files", [])),
                setup_command=tuple(str(v) for v in raw.get("setup_command", [])),
                test_command=tuple(str(v) for v in raw.get("test_command", [])),
                hidden_checks=tuple(str(v) for v in raw.get("hidden_checks", [])),
            )
        )
    return tasks


def _rg_matches(repo: Path, task: str, target: str | None) -> list[dict[str, Any]]:
    terms: list[str] = []
    if target:
        terms.append(target)
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
        path_text = ((data.get("path") or {}).get("text") or "")
        try:
            rel = Path(path_text).resolve().relative_to(repo).as_posix()
        except ValueError:
            continue
        line_text = ((data.get("lines") or {}).get("text") or "")
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
    target_lower = (target or "").lower()
    tokens = set(query_tokenizer.tokenize(task))

    def score(match: dict[str, Any]) -> tuple[float, str, int]:
        text = str(match["text"]).lower()
        path = str(match["path"]).lower()
        value = 0.0
        if target_lower and target_lower in text:
            value += 50.0
        if target_lower and target_lower in path:
            value += 30.0
        if path in task_lower:
            value += 20.0
        value += 3.0 * len(tokens & set(query_tokenizer.tokenize(text)))
        if re.search(r"^\s*(?:async\s+)?(?:def|class|function|export)\b", text):
            value += 12.0
        if "/test" in f"/{path}" and not (tokens & {"test", "tests", "failing"}):
            value -= 8.0
        return (-value, path, int(match["line"]))

    return sorted(matches, key=score)


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
