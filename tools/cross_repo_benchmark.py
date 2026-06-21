"""Native MCP cross-repo benchmark.

This script measures the same stdio JSON-RPC path used by coding agents. It
intentionally avoids importing CseGraph internals; all CseGraph work is done by
spawning the MCP server and calling tools through ``mcp.client``.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from platform import platform
from typing import Any, Iterable, Sequence

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parents[1]
SANDBOX_DIR = REPO_ROOT / "sandbox"

DEFAULT_REPOS = (
    "nanoGPT",
    "micrograd",
    "django",
    "pandas",
    "flask",
    "transformers",
    "scikit-learn",
    "fastapi",
    "celery",
    "pytest",
)

SOURCE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".lua",
    ".md",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".sh",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
SOURCE_FILENAMES = {
    "Dockerfile",
    "Makefile",
    "Pipfile",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
}
SKIP_DIRS = {
    ".agents",
    ".cache",
    ".codex",
    ".csegraph",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".scratch",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "benchmark_results",
    "build",
    "dist",
    "env",
    "htmlcov",
    "node_modules",
    "sandbox",
    "venv",
}
LOW_VALUE_MUTATION_DIRS = {
    "benchmark",
    "benchmarks",
    "doc",
    "docs",
    "documentation",
    "example",
    "examples",
    "sample",
    "samples",
    "script",
    "scripts",
}
LOW_VALUE_MUTATION_FILENAMES = {
    "conf.py",
    "conftest.py",
    "noxfile.py",
    "setup.py",
}

CLASS_PATTERNS = (
    re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)", re.M),
    re.compile(r"^\s*(?:interface|struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M),
)
FUNCTION_PATTERNS = (
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)", re.M),
    re.compile(
        r"^\s*(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\(",
        re.M,
    ),
)


def load_openai_tokenizer() -> tuple[Any | None, str]:
    model = os.environ.get("CSEGRAPH_BENCH_TOKENIZER_MODEL", "").strip()
    encoding_name = os.environ.get(
        "CSEGRAPH_BENCH_OPENAI_ENCODING",
        os.environ.get("CSEGRAPH_BENCH_TOKENIZER", "o200k_base"),
    ).strip()
    try:
        import tiktoken
    except Exception:  # pragma: no cover - optional dependency fallback
        return None, "unavailable (install `.[benchmark]` for tiktoken)"

    try:
        if model:
            encoding = tiktoken.encoding_for_model(model)
            name = getattr(encoding, "name", "unknown")
            return encoding, f"tiktoken:model={model};encoding={name}"
        encoding = tiktoken.get_encoding(encoding_name)
        return encoding, f"tiktoken:encoding={encoding_name}"
    except Exception as exc:  # pragma: no cover - defensive fallback for unknown models
        return None, f"unavailable ({exc})"


_OPENAI_TOKENIZER, _OPENAI_TOKENIZER_LABEL = load_openai_tokenizer()


@dataclass(frozen=True)
class TokenCounts:
    chars4: int
    openai_o200k: int | None


@dataclass(frozen=True)
class RepoSnapshot:
    files: int
    bytes: int
    lines: int
    chars4_tokens: int
    openai_o200k_tokens: int | None
    file_names: list[str]
    class_names: list[str]
    function_names: list[str]
    full_read_latency_ms: float
    tokenization_latency_ms: float


@dataclass(frozen=True)
class ToolCallMetrics:
    tool: str
    latency_ms: float
    content_text: str
    content_bytes: int
    content_chars4_tokens: int
    content_openai_o200k_tokens: int | None
    envelope_bytes: int
    envelope_chars4_tokens: int
    envelope_openai_o200k_tokens: int | None
    tokenization_ms: float


@dataclass(frozen=True)
class PhaseBResult:
    target: str | None
    refresh_ms: float
    context_ms: float
    total_ms: float
    mcp_chars4_tokens: int
    mcp_openai_o200k_tokens: int | None
    tokenization_ms: float
    skipped_reason: str | None = None


class NativeMcpClient:
    """Small wrapper around the official MCP stdio client."""

    def __init__(self, command: str, args: Sequence[str]) -> None:
        self.command = command
        self.args = list(args)
        self.startup_ms = 0.0
        self._stdio_cm: Any = None
        self._session_cm: Any = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "NativeMcpClient":
        params = StdioServerParameters(
            command=self.command,
            args=self.args,
            cwd=REPO_ROOT,
            env=dict(os.environ),
        )
        start = time.perf_counter()
        self._stdio_cm = stdio_client(params)
        read_stream, write_stream = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read_stream, write_stream)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()
        self.startup_ms = elapsed_ms(start)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._session_cm is not None:
            await self._session_cm.__aexit__(exc_type, exc, tb)
        if self._stdio_cm is not None:
            await self._stdio_cm.__aexit__(exc_type, exc, tb)

    async def list_tool_names(self) -> list[str]:
        session = self._require_session()
        result = await session.list_tools()
        return [tool.name for tool in result.tools]

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> ToolCallMetrics:
        session = self._require_session()
        start = time.perf_counter()
        result = await session.call_tool(tool, arguments=arguments)
        latency_ms = elapsed_ms(start)
        content_text = extract_content_text(result)
        envelope_json = serialize_mcp_result(result)
        token_start = time.perf_counter()
        content_tokens = count_token_metrics(content_text)
        envelope_tokens = count_token_metrics(envelope_json)
        tokenization_ms = elapsed_ms(token_start)
        metrics = ToolCallMetrics(
            tool=tool,
            latency_ms=latency_ms,
            content_text=content_text,
            content_bytes=len(content_text.encode("utf-8")),
            content_chars4_tokens=content_tokens.chars4,
            content_openai_o200k_tokens=content_tokens.openai_o200k,
            envelope_bytes=len(envelope_json.encode("utf-8")),
            envelope_chars4_tokens=envelope_tokens.chars4,
            envelope_openai_o200k_tokens=envelope_tokens.openai_o200k,
            tokenization_ms=tokenization_ms,
        )
        if getattr(result, "isError", False):
            raise RuntimeError(f"{tool} failed over MCP: {content_text}")
        return metrics

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("MCP client session is not initialized")
        return self._session


def elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def count_chars4_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def count_openai_o200k_tokens(text: str) -> int | None:
    if not text:
        return 0
    if _OPENAI_TOKENIZER is None:
        return None
    return len(_OPENAI_TOKENIZER.encode(text, disallowed_special=()))


def count_token_metrics(text: str) -> TokenCounts:
    return TokenCounts(
        chars4=count_chars4_tokens(text),
        openai_o200k=count_openai_o200k_tokens(text),
    )


def add_optional(left: int | None, right: int | None) -> int | None:
    if left is None or right is None:
        return None
    return left + right


def multiply_optional(value: int | None, multiplier: int) -> int | None:
    if value is None:
        return None
    return value * multiplier


def extract_content_text(result: Any) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(text)
        elif hasattr(item, "model_dump_json"):
            parts.append(item.model_dump_json())
        else:
            parts.append(str(item))
    return "\n".join(parts)


def serialize_mcp_result(result: Any) -> str:
    if hasattr(result, "model_dump_json"):
        return result.model_dump_json()
    if hasattr(result, "model_dump"):
        return json.dumps(result.model_dump(), sort_keys=True, default=str)
    return json.dumps(result, sort_keys=True, default=str)


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def repo_names_from_env() -> list[str]:
    raw = os.environ.get("CSEGRAPH_BENCH_REPOS")
    if not raw:
        return list(DEFAULT_REPOS)
    return [name.strip() for name in raw.split(",") if name.strip()]


def server_command_from_env() -> tuple[str, list[str]]:
    command = os.environ.get("CSEGRAPH_MCP_COMMAND", sys.executable)
    args = shlex.split(os.environ.get("CSEGRAPH_MCP_ARGS", "-m csegraph._cli serve"))
    return command, args


def git_value(args: Sequence[str], *, cwd: Path = REPO_ROOT) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def git_files(repo_path: Path, *, include_untracked: bool) -> list[Path] | None:
    cmd = ["git", "-C", str(repo_path), "ls-files", "-z"]
    if include_untracked:
        cmd = ["git", "-C", str(repo_path), "ls-files", "-co", "--exclude-standard", "-z"]
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    paths = [raw.decode("utf-8", errors="replace") for raw in result.stdout.split(b"\0") if raw]
    return [repo_path / path for path in paths]


def benchmark_files(repo_path: Path, *, include_untracked: bool = True) -> Iterable[Path]:
    git_candidates = git_files(repo_path, include_untracked=include_untracked)
    candidates = git_candidates if git_candidates is not None else repo_path.rglob("*")
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        try:
            rel_parts = path.resolve().relative_to(repo_path.resolve()).parts
        except ValueError:
            continue
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if path.suffix not in SOURCE_EXTENSIONS and path.name not in SOURCE_FILENAMES:
            continue
        yield path


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
    except OSError:
        return None


def collect_repo_snapshot(repo_path: Path) -> RepoSnapshot:
    read_latency_ms = 0.0
    tokenization_latency_ms = 0.0
    total_bytes = 0
    total_lines = 0
    total_chars4_tokens = 0
    total_openai_o200k_tokens: int | None = 0 if _OPENAI_TOKENIZER is not None else None
    file_names: list[str] = []
    class_names: list[str] = []
    function_names: list[str] = []
    max_query_scan_files = env_int("CSEGRAPH_BENCH_QUERY_SCAN_FILES", 5_000)

    for index, path in enumerate(benchmark_files(repo_path, include_untracked=True)):
        read_start = time.perf_counter()
        text = read_text(path)
        read_latency_ms += elapsed_ms(read_start)
        if text is None:
            continue
        rel = path.relative_to(repo_path).as_posix()
        encoded = text.encode("utf-8", errors="ignore")
        total_bytes += len(encoded)
        total_lines += len(text.splitlines())
        token_start = time.perf_counter()
        tokens = count_token_metrics(text)
        total_chars4_tokens += tokens.chars4
        total_openai_o200k_tokens = add_optional(total_openai_o200k_tokens, tokens.openai_o200k)
        tokenization_latency_ms += elapsed_ms(token_start)
        file_names.append(rel)
        if index < max_query_scan_files:
            class_names.extend(extract_names(text, CLASS_PATTERNS))
            function_names.extend(extract_names(text, FUNCTION_PATTERNS))

    return RepoSnapshot(
        files=len(file_names),
        bytes=total_bytes,
        lines=total_lines,
        chars4_tokens=total_chars4_tokens,
        openai_o200k_tokens=total_openai_o200k_tokens,
        file_names=dedupe(file_names),
        class_names=dedupe(class_names),
        function_names=dedupe(function_names),
        full_read_latency_ms=read_latency_ms,
        tokenization_latency_ms=tokenization_latency_ms,
    )


def extract_names(text: str, patterns: Sequence[re.Pattern[str]]) -> list[str]:
    names: list[str] = []
    for pattern in patterns:
        names.extend(match.group(1) for match in pattern.finditer(text))
    return names


def dedupe(items: Iterable[str], *, limit: int = 2_000) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def generate_queries(snapshot: RepoSnapshot, *, limit: int) -> list[str]:
    files = snapshot.file_names or ["main.py"]
    classes = snapshot.class_names or ["App"]
    functions = snapshot.function_names or ["init"]
    random.seed(42)
    queries: set[str] = set()

    vague_templates = [
        "what files are responsible for {class_name}?",
        "where does the system handle {class_name}?",
        "how is {function} implemented?",
        "show me everything related to {class_name} in the repository",
        "which modules are responsible for {function}?",
        "is there any code for {class_name}?",
        "find all places that mention {file}",
    ]
    structural_templates = [
        "which function inside {file} is responsible for {class_name}?",
        "what components depend on {class_name}?",
        "how does {class_name} interact with {function}?",
        "where is {function} called from within {file}?",
        "how does {file} use {class_name}?",
        "what are the downstream dependents of {class_name}?",
        "trace the usage of {function} throughout the project",
    ]
    hardcore_templates = [
        "fix tie-breaking logic in {function} to prioritize {class_name}",
        "explain the exact algorithm inside {class_name} that calculates {function}",
        "how are {function} values stored in {file} and queried by {class_name}?",
        "what happens when {class_name} encounters an error during {function}?",
        "refactor {function} in {file} to use {class_name}",
        "write a new test for {function} covering {class_name}",
        "how does recursive logic in {file} prevent infinite loops for {class_name}?",
    ]

    attempts = 0
    max_attempts = max(1_000, limit * 50)
    while len(queries) < limit and attempts < max_attempts:
        attempts += 1
        bucket = random.random()
        if bucket < 0.3:
            template = random.choice(vague_templates)
        elif bucket < 0.7:
            template = random.choice(structural_templates)
        else:
            template = random.choice(hardcore_templates)
        queries.add(
            template.format(
                file=random.choice(files),
                class_name=random.choice(classes),
                function=random.choice(functions),
            )
        )

    return list(queries)[:limit]


async def run_context_query(
    client: NativeMcpClient,
    query: str,
    repo_path: Path,
    db_path: Path,
    *,
    profile: str,
    detail_level: str,
) -> ToolCallMetrics:
    return await client.call_tool(
        "csegraph_context",
        {
            "task": query,
            "repo": str(repo_path),
            "db": str(db_path),
            "profile": profile,
            "detail_level": detail_level,
        },
    )


async def run_phase_b(
    client: NativeMcpClient,
    repo_path: Path,
    db_path: Path,
    *,
    profile: str,
    postprocess_level: str,
    detail_level: str,
) -> PhaseBResult:
    target_file = find_mutation_target(repo_path)
    if target_file is None:
        return PhaseBResult(
            target=None,
            refresh_ms=0.0,
            context_ms=0.0,
            total_ms=0.0,
            mcp_chars4_tokens=0,
            mcp_openai_o200k_tokens=0 if _OPENAI_TOKENIZER is not None else None,
            tokenization_ms=0.0,
            skipped_reason="no tracked Python source file found",
        )

    original_content = read_text(target_file)
    if original_content is None:
        return PhaseBResult(
            target=target_file.relative_to(repo_path).as_posix(),
            refresh_ms=0.0,
            context_ms=0.0,
            total_ms=0.0,
            mcp_chars4_tokens=0,
            mcp_openai_o200k_tokens=0 if _OPENAI_TOKENIZER is not None else None,
            tokenization_ms=0.0,
            skipped_reason="target file was not UTF-8 readable",
        )

    mutation = "\n\ndef __csegraph_dummy_test_method():\n    return None\n"
    target_rel = target_file.relative_to(repo_path).as_posix()
    try:
        target_file.write_text(original_content + mutation, encoding="utf-8")
        refresh = await client.call_tool(
            "csegraph_refresh",
            {
                "repo": str(repo_path),
                "db": str(db_path),
                "profile": profile,
                "postprocess_level": postprocess_level,
            },
        )
        context = await run_context_query(
            client,
            "what does __csegraph_dummy_test_method do?",
            repo_path,
            db_path,
            profile=profile,
            detail_level=detail_level,
        )
        return PhaseBResult(
            target=target_rel,
            refresh_ms=refresh.latency_ms,
            context_ms=context.latency_ms,
            total_ms=refresh.latency_ms + context.latency_ms,
            mcp_chars4_tokens=refresh.content_chars4_tokens + context.content_chars4_tokens,
            mcp_openai_o200k_tokens=add_optional(
                refresh.content_openai_o200k_tokens,
                context.content_openai_o200k_tokens,
            ),
            tokenization_ms=refresh.tokenization_ms + context.tokenization_ms,
        )
    finally:
        target_file.write_text(original_content, encoding="utf-8")
        try:
            await client.call_tool(
                "csegraph_refresh",
                {
                    "repo": str(repo_path),
                    "db": str(db_path),
                    "profile": profile,
                    "postprocess_level": postprocess_level,
                },
            )
        except Exception as exc:
            print(f"Cleanup refresh failed for {repo_path.name}: {exc}")


def find_mutation_target(repo_path: Path) -> Path | None:
    tracked = git_files(repo_path, include_untracked=False)
    candidates = tracked if tracked is not None else list(benchmark_files(repo_path))
    python_files = [
        path
        for path in candidates
        if path.suffix == ".py"
        and path.exists()
        and path.is_file()
        and not path.name.startswith("_")
        and not any("test" in part.lower() for part in path.relative_to(repo_path).parts)
    ]
    if not python_files:
        return None
    return sorted(python_files, key=lambda path: mutation_target_rank(repo_path, path))[0]


def mutation_target_rank(repo_path: Path, path: Path) -> tuple[int, int, str]:
    rel = path.relative_to(repo_path)
    rel_parts = tuple(part.lower() for part in rel.parts)
    low_value_penalty = 1 if any(part in LOW_VALUE_MUTATION_DIRS for part in rel_parts) else 0
    if path.name.lower() in LOW_VALUE_MUTATION_FILENAMES:
        low_value_penalty += 1
    return (low_value_penalty, len(rel.parts), rel.as_posix())


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil((pct / 100) * len(ordered)) - 1))
    return ordered[index]


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def ratio_optional(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    return numerator / max(denominator, 1)


def format_optional_int(value: int | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value:,}"


def format_optional_ratio(value: float | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value:.1f}x reduction"


def format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{value}B"


async def run_repo(
    client: NativeMcpClient,
    repo_name: str,
    report_path: Path,
    *,
    profile: str,
    postprocess_level: str,
    detail_level: str,
    query_limit: int,
) -> dict[str, Any] | None:
    repo_path = SANDBOX_DIR / repo_name
    if not repo_path.exists():
        print(f"Skipping {repo_name}, directory not found.")
        return None

    db_path = repo_path / ".csegraph" / "index.db"
    print("\n=========================================")
    print(f"Repository: {repo_name}")
    print("Scanning repository for naive full-read baseline...")
    snapshot = collect_repo_snapshot(repo_path)
    queries = generate_queries(snapshot, limit=query_limit)
    print(
        f"Repository stats: {snapshot.files:,} files, "
        f"{format_bytes(snapshot.bytes)}, {snapshot.lines:,} lines."
    )
    print(f"Generated {len(queries)} tailored queries for {repo_name}.")

    print(f"Indexing {repo_name} through MCP...")
    index_metrics = await client.call_tool(
        "csegraph_index",
        {
            "repo": str(repo_path),
            "db": str(db_path),
            "profile": profile,
            "postprocess_level": postprocess_level,
        },
    )

    mcp_latencies: list[float] = []
    mcp_content_chars4_tokens = 0
    mcp_content_openai_o200k_tokens: int | None = 0 if _OPENAI_TOKENIZER is not None else None
    mcp_envelope_chars4_tokens = 0
    mcp_envelope_openai_o200k_tokens: int | None = 0 if _OPENAI_TOKENIZER is not None else None
    mcp_content_bytes = 0
    mcp_tokenization_ms = 0.0
    errors: list[str] = []

    for index, query in enumerate(queries, 1):
        print(f"\r[{repo_name}] MCP context query {index}/{len(queries)}...", end="", flush=True)
        try:
            metrics = await run_context_query(
                client,
                query,
                repo_path,
                db_path,
                profile=profile,
                detail_level=detail_level,
            )
        except Exception as exc:
            errors.append(f"{query}: {exc}")
            continue
        mcp_latencies.append(metrics.latency_ms)
        mcp_content_chars4_tokens += metrics.content_chars4_tokens
        mcp_content_openai_o200k_tokens = add_optional(
            mcp_content_openai_o200k_tokens,
            metrics.content_openai_o200k_tokens,
        )
        mcp_envelope_chars4_tokens += metrics.envelope_chars4_tokens
        mcp_envelope_openai_o200k_tokens = add_optional(
            mcp_envelope_openai_o200k_tokens,
            metrics.envelope_openai_o200k_tokens,
        )
        mcp_content_bytes += metrics.content_bytes
        mcp_tokenization_ms += metrics.tokenization_ms

    print(f"\nPhase A completed for {repo_name}.")
    print(f"Running Phase B mutation refresh for {repo_name} through MCP...")
    phase_b = await run_phase_b(
        client,
        repo_path,
        db_path,
        profile=profile,
        postprocess_level=postprocess_level,
        detail_level=detail_level,
    )

    completed_queries = len(mcp_latencies)
    naive_chars4_total = snapshot.chars4_tokens * completed_queries
    naive_bytes_total = snapshot.bytes * completed_queries
    chars4_ratio = naive_chars4_total / max(mcp_content_chars4_tokens, 1)
    naive_openai_o200k_total = multiply_optional(snapshot.openai_o200k_tokens, completed_queries)
    openai_o200k_ratio = ratio_optional(naive_openai_o200k_total, mcp_content_openai_o200k_tokens)

    with report_path.open("a", encoding="utf-8") as report:
        report.write(f"## {repo_name.upper()}\n")
        report.write(
            f"- **Repository Size**: {snapshot.files:,} source/text files, "
            f"{format_bytes(snapshot.bytes)}, {snapshot.lines:,} lines\n"
        )
        report.write("- **Canonical Size Metric**: exact UTF-8 bytes\n")
        report.write("- **Heuristic Token Metric**: CseGraph `chars/4`\n")
        report.write(f"- **OpenAI Proxy Tokenizer**: {_OPENAI_TOKENIZER_LABEL}\n")
        report.write(f"- **Index MCP Round Trip**: {index_metrics.latency_ms:.1f}ms\n")
        report.write(
            f"- **Naive Full-Repo Read Sample**: {snapshot.full_read_latency_ms:.1f}ms read-only, "
            f"{snapshot.chars4_tokens:,} chars/4 tokens per query, "
            f"{format_optional_int(snapshot.openai_o200k_tokens)} OpenAI proxy tokens per query\n"
        )
        report.write(
            f"- **Naive Token Counting Time**: {snapshot.tokenization_latency_ms:.1f}ms "
            "per full-repo sample\n"
        )
        report.write(f"- **Queries Completed**: {len(mcp_latencies):,}/{len(queries):,}\n")
        report.write(f"- **Average MCP Context Latency**: {mean(mcp_latencies):.1f}ms\n")
        report.write(f"- **P50 MCP Context Latency**: {percentile(mcp_latencies, 50):.1f}ms\n")
        report.write(f"- **P95 MCP Context Latency**: {percentile(mcp_latencies, 95):.1f}ms\n")
        report.write(
            f"- **MCP Context Token Counting Time**: {mcp_tokenization_ms:.1f}ms "
            "(excluded from MCP latency)\n"
        )
        report.write(f"- **Total MCP Content Bytes**: {mcp_content_bytes:,}\n")
        report.write(f"- **Total Naive chars/4 Tokens**: {naive_chars4_total:,}\n")
        report.write(f"- **Total MCP Content chars/4 Tokens**: {mcp_content_chars4_tokens:,}\n")
        report.write(f"- **Total MCP Envelope chars/4 Tokens**: {mcp_envelope_chars4_tokens:,}\n")
        report.write(f"- **chars/4 Token Efficiency**: {chars4_ratio:.1f}x reduction\n")
        report.write(
            f"- **Total Naive OpenAI Proxy Tokens**: "
            f"{format_optional_int(naive_openai_o200k_total)}\n"
        )
        report.write(
            f"- **Total MCP Content OpenAI Proxy Tokens**: "
            f"{format_optional_int(mcp_content_openai_o200k_tokens)}\n"
        )
        report.write(
            f"- **Total MCP Envelope OpenAI Proxy Tokens**: "
            f"{format_optional_int(mcp_envelope_openai_o200k_tokens)}\n"
        )
        report.write(f"- **OpenAI Proxy Token Efficiency**: {format_optional_ratio(openai_o200k_ratio)}\n")
        if phase_b.skipped_reason:
            report.write(f"- **Phase B Mutation**: skipped ({phase_b.skipped_reason})\n")
        else:
            report.write(f"- **Phase B Target**: `{phase_b.target}`\n")
            report.write(f"- **Phase B Refresh MCP Round Trip**: {phase_b.refresh_ms:.1f}ms\n")
            report.write(f"- **Phase B Context MCP Round Trip**: {phase_b.context_ms:.1f}ms\n")
            report.write(f"- **Phase B Total MCP Round Trip**: {phase_b.total_ms:.1f}ms\n")
            report.write(f"- **Phase B MCP chars/4 Tokens**: {phase_b.mcp_chars4_tokens:,}\n")
            report.write(
                f"- **Phase B MCP OpenAI Proxy Tokens**: "
                f"{format_optional_int(phase_b.mcp_openai_o200k_tokens)}\n"
            )
            report.write(
                f"- **Phase B Token Counting Time**: {phase_b.tokenization_ms:.1f}ms "
                "(excluded from MCP latency)\n"
            )
        if errors:
            report.write(f"- **Errors**: {len(errors):,}\n")
            for error in errors[:5]:
                report.write(f"  - {error[:300]}\n")
        report.write("\n")

    return {
        "repo": repo_name,
        "repo_path": str(repo_path),
        "profile": profile,
        "postprocess_level": postprocess_level,
        "detail_level": detail_level,
        "query_limit": query_limit,
        "queries_generated": len(queries),
        "queries_completed": completed_queries,
        "errors": errors,
        "repository": {
            "source_text_files": snapshot.files,
            "bytes": snapshot.bytes,
            "lines": snapshot.lines,
            "chars4_tokens": snapshot.chars4_tokens,
            "openai_o200k_tokens": snapshot.openai_o200k_tokens,
            "full_read_latency_ms": snapshot.full_read_latency_ms,
            "tokenization_latency_ms": snapshot.tokenization_latency_ms,
        },
        "mcp": {
            "index_latency_ms": index_metrics.latency_ms,
            "avg_context_latency_ms": mean(mcp_latencies),
            "p50_context_latency_ms": percentile(mcp_latencies, 50),
            "p95_context_latency_ms": percentile(mcp_latencies, 95),
            "context_content_bytes": mcp_content_bytes,
            "context_content_chars4_tokens": mcp_content_chars4_tokens,
            "context_content_openai_o200k_tokens": mcp_content_openai_o200k_tokens,
            "context_envelope_chars4_tokens": mcp_envelope_chars4_tokens,
            "context_envelope_openai_o200k_tokens": mcp_envelope_openai_o200k_tokens,
            "context_tokenization_ms": mcp_tokenization_ms,
        },
        "naive": {
            "bytes_total": naive_bytes_total,
            "chars4_tokens_total": naive_chars4_total,
            "openai_o200k_tokens_total": naive_openai_o200k_total,
        },
        "efficiency": {
            "chars4_reduction": chars4_ratio,
            "openai_o200k_reduction": openai_o200k_ratio,
        },
        "phase_b": {
            "target": phase_b.target,
            "skipped_reason": phase_b.skipped_reason,
            "refresh_ms": phase_b.refresh_ms,
            "context_ms": phase_b.context_ms,
            "total_ms": phase_b.total_ms,
            "mcp_chars4_tokens": phase_b.mcp_chars4_tokens,
            "mcp_openai_o200k_tokens": phase_b.mcp_openai_o200k_tokens,
            "tokenization_ms": phase_b.tokenization_ms,
        },
    }


async def main_async() -> None:
    report_path = Path(
        os.environ.get(
            "CSEGRAPH_CROSS_REPO_REPORT",
            str(REPO_ROOT / "benchmark_results" / "native_mcp_cross_repo_results.md"),
        )
    )
    json_path = Path(
        os.environ.get(
            "CSEGRAPH_CROSS_REPO_JSON",
            str(REPO_ROOT / "benchmark_results" / "native_mcp_cross_repo_results.json"),
        )
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    profile = os.environ.get("CSEGRAPH_BENCH_PROFILE", "auto")
    postprocess_level = os.environ.get("CSEGRAPH_BENCH_POSTPROCESS_LEVEL", "minimal")
    detail_level = os.environ.get("CSEGRAPH_BENCH_DETAIL_LEVEL", "standard")
    query_limit = env_int("CSEGRAPH_BENCH_QUERY_LIMIT", 100)
    repo_names = repo_names_from_env()
    command, args = server_command_from_env()
    started_at = datetime.now(timezone.utc).isoformat()
    metadata: dict[str, Any] = {
        "started_at_utc": started_at,
        "csegraph_commit": git_value(["rev-parse", "--short=12", "HEAD"]) or "unknown",
        "git_branch": git_value(["rev-parse", "--abbrev-ref", "HEAD"]) or "unknown",
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform(),
        "server_command": command,
        "server_args": args,
        "profile": profile,
        "postprocess_level": postprocess_level,
        "detail_level": detail_level,
        "query_limit_per_repo": query_limit,
        "repositories": repo_names,
        "canonical_metric": "exact UTF-8 bytes",
        "heuristic_metric": "chars/4",
        "openai_proxy_metric": _OPENAI_TOKENIZER_LABEL,
        "transport": "MCP stdio via mcp.client ClientSession.call_tool",
        "workload_root": str(SANDBOX_DIR),
    }

    with report_path.open("w", encoding="utf-8") as report:
        report.write("# Native MCP Cross-Repo Benchmark Results\n\n")
        report.write(
            "This benchmark launches the CseGraph MCP server as a separate stdio "
            "process and calls tools through the official `mcp.client` JSON-RPC path. "
            "It does not import CseGraph SDK internals for indexing or retrieval.\n\n"
        )
        report.write("## Methodology\n")
        report.write(
            "- MCP latency is measured as the client-side round trip around "
            "`session.call_tool(...)`; token counting and report writing are excluded.\n"
        )
        report.write(
            "- Token counts are computed outside the CseGraph server process after MCP "
            "responses return; token-counting time is reported separately.\n"
        )
        report.write(
            "- Exact UTF-8 byte counts are the canonical, provider-neutral size metric.\n"
        )
        report.write(
            "- `chars/4` counts are CseGraph's simple transparent heuristic and are "
            "reported separately from tokenizer-specific counts.\n"
        )
        report.write(
            "- OpenAI proxy counts use local `tiktoken` with `o200k_base` by default. "
            "Set `CSEGRAPH_BENCH_TOKENIZER_MODEL` for model-specific OpenAI mappings or "
            "`CSEGRAPH_BENCH_OPENAI_ENCODING` for another OpenAI encoding.\n"
        )
        report.write(
            "- Claude and Gemini exact token counts require their provider token-count APIs. "
            "For Gemini 3.1 Pro Preview, that means a separate provider audit using "
            "`gemini-3.1-pro-preview` with Google's `count_tokens`, not `tiktoken`. "
            "Composer/Cursor does not publish a standalone tokenizer/count API; for those "
            "surfaces, this benchmark reports exact bytes plus clearly labeled heuristic "
            "and OpenAI-proxy counts.\n"
        )
        report.write(
            "- The naive baseline reads all included source/text files once per query; "
            "its read time and token-counting time are reported separately.\n\n"
        )
        report.write("## Run Metadata\n")
        report.write(f"- **Started At UTC**: {started_at}\n")
        report.write(f"- **CseGraph Commit**: `{metadata['csegraph_commit']}`\n")
        report.write(f"- **Git Branch**: `{metadata['git_branch']}`\n")
        report.write(f"- **Python**: `{sys.version.split()[0]}` at `{sys.executable}`\n")
        report.write(f"- **Platform**: `{platform()}`\n")
        report.write(f"- **Server Command**: `{command} {' '.join(args)}`\n")
        report.write(f"- **Index Profile**: `{profile}`\n")
        report.write(f"- **Postprocess Level**: `{postprocess_level}`\n")
        report.write(f"- **Context Detail Level**: `{detail_level}`\n")
        report.write(f"- **Query Limit Per Repo**: {query_limit:,}\n")
        report.write("- **Canonical Metric**: exact UTF-8 bytes\n")
        report.write("- **Heuristic Metric**: CseGraph `chars/4`\n")
        report.write(f"- **OpenAI Proxy Metric**: `{_OPENAI_TOKENIZER_LABEL}`\n")
        report.write(
            "- **Provider-Native Metrics**: Claude/Gemini require separate provider API audits; "
            "Composer/Cursor are not labeled as exact tokenizer counts here\n\n"
        )

    repo_results: list[dict[str, Any]] = []
    mcp_startup_ms = 0.0
    tool_names: list[str] = []
    async with NativeMcpClient(command, args) as client:
        mcp_startup_ms = client.startup_ms
        tool_names = await client.list_tool_names()
        required = {"csegraph_index", "csegraph_refresh", "csegraph_context"}
        missing = required - set(tool_names)
        if missing:
            raise RuntimeError(f"MCP server is missing required tools: {sorted(missing)}")
        with report_path.open("a", encoding="utf-8") as report:
            report.write(f"- **MCP Session Startup**: {client.startup_ms:.1f}ms\n")
            report.write(f"- **MCP Tools Exposed**: {', '.join(tool_names)}\n\n")

        for repo_name in repo_names:
            repo_result = await run_repo(
                client,
                repo_name,
                report_path,
                profile=profile,
                postprocess_level=postprocess_level,
                detail_level=detail_level,
                query_limit=query_limit,
            )
            if repo_result is not None:
                repo_results.append(repo_result)

    if not repo_results:
        raise RuntimeError(
            f"No sandbox repositories were benchmarked under {SANDBOX_DIR}. "
            "Populate sandbox/ or set CSEGRAPH_BENCH_REPOS to existing directories."
        )

    payload = {
        "metadata": metadata | {
            "mcp_startup_ms": mcp_startup_ms,
            "mcp_tools": tool_names,
        },
        "summary": summarize_repo_results(repo_results),
        "repositories": repo_results,
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nBenchmark report written to {report_path}")
    print(f"Benchmark JSON written to {json_path}")


def summarize_repo_results(repo_results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    completed = sum(int(repo["queries_completed"]) for repo in repo_results)
    requested = sum(int(repo["queries_generated"]) for repo in repo_results)
    naive_bytes = sum(int(repo["naive"]["bytes_total"]) for repo in repo_results)
    naive_chars4 = sum(int(repo["naive"]["chars4_tokens_total"]) for repo in repo_results)
    mcp_bytes = sum(int(repo["mcp"]["context_content_bytes"]) for repo in repo_results)
    mcp_chars4 = sum(int(repo["mcp"]["context_content_chars4_tokens"]) for repo in repo_results)
    naive_openai = sum_optional(repo["naive"]["openai_o200k_tokens_total"] for repo in repo_results)
    mcp_openai = sum_optional(
        repo["mcp"]["context_content_openai_o200k_tokens"] for repo in repo_results
    )
    latencies = [float(repo["mcp"]["avg_context_latency_ms"]) for repo in repo_results]
    phase_b_totals = [
        float(repo["phase_b"]["total_ms"])
        for repo in repo_results
        if repo["phase_b"]["skipped_reason"] is None
    ]
    reductions = [float(repo["efficiency"]["chars4_reduction"]) for repo in repo_results]
    best = max(repo_results, key=lambda repo: repo["efficiency"]["chars4_reduction"], default=None)
    worst = min(repo_results, key=lambda repo: repo["efficiency"]["chars4_reduction"], default=None)
    return {
        "repositories": len(repo_results),
        "queries_requested": requested,
        "queries_completed": completed,
        "errors": sum(len(repo["errors"]) for repo in repo_results),
        "total_naive_bytes": naive_bytes,
        "total_mcp_content_bytes": mcp_bytes,
        "total_naive_chars4_tokens": naive_chars4,
        "total_mcp_content_chars4_tokens": mcp_chars4,
        "aggregate_chars4_reduction": ratio_optional(naive_chars4, mcp_chars4),
        "total_naive_openai_o200k_tokens": naive_openai,
        "total_mcp_content_openai_o200k_tokens": mcp_openai,
        "aggregate_openai_o200k_reduction": ratio_optional(naive_openai, mcp_openai),
        "unweighted_avg_chars4_reduction": mean(reductions),
        "unweighted_avg_mcp_context_latency_ms": mean(latencies),
        "unweighted_avg_phase_b_total_ms": mean(phase_b_totals),
        "best_chars4_reduction_repo": best["repo"] if best else None,
        "best_chars4_reduction": best["efficiency"]["chars4_reduction"] if best else None,
        "worst_chars4_reduction_repo": worst["repo"] if worst else None,
        "worst_chars4_reduction": worst["efficiency"]["chars4_reduction"] if worst else None,
    }


def sum_optional(values: Iterable[int | None]) -> int | None:
    total: int | None = 0 if _OPENAI_TOKENIZER is not None else None
    for value in values:
        total = add_optional(total, value)
    return total


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
