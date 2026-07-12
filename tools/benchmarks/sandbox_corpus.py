"""Build repo-specific sandbox tasks from direct source inspection."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from tools.benchmarks.models import AdaptiveBenchmarkCorpus
from tools.benchmarks.sandbox import SANDBOX_REPOSITORIES, SandboxRepositorySpec
from tools.benchmarks.schema import TASK_SCHEMA_VERSION_V2, load_corpus

TASKS_PER_TIER = {"tiny": 12, "small": 20, "medium": 30, "large": 45}


@dataclass(frozen=True)
class _Symbol:
    path: str
    line: int
    end_line: int
    name: str
    kind: str
    symbol_id: str


def build_sandbox_corpus(
    repo_root: Path,
    *,
    specs: Iterable[SandboxRepositorySpec] = SANDBOX_REPOSITORIES,
) -> AdaptiveBenchmarkCorpus:
    root = Path(repo_root).resolve()
    selected_specs = tuple(specs)
    tasks: list[dict[str, object]] = []
    repositories = {spec.path: {"url": spec.url, "commit": spec.commit} for spec in selected_specs}
    for spec in selected_specs:
        repository = root / spec.path
        if not repository.is_dir():
            raise FileNotFoundError(
                f"sandbox repository {spec.path!r} is missing; run "
                "tools/bootstrap_benchmark_sandbox.py first"
            )
        symbols = _source_symbols(repository, spec)
        public_symbols = [
            symbol
            for symbol in symbols
            if not symbol.name.startswith("_") and symbol.kind in {"class", "function"}
        ]
        if public_symbols:
            symbols = public_symbols
        target_count = TASKS_PER_TIER[spec.size_tier]
        if not symbols:
            raise ValueError(f"sandbox repository {spec.path!r} has no supported symbols")
        for position in range(target_count):
            symbol = symbols[position % len(symbols)]
            category, template = spec.scenario_templates[position % len(spec.scenario_templates)]
            prompt = f"{template.format(name=symbol.name)} in {symbol.path}"
            task_id = _task_id(spec.name, category, symbol, position)
            start = max(1, symbol.line - 16)
            end = symbol.line + 32
            tasks.append(
                {
                    "id": task_id,
                    "repo": spec.path,
                    "commit": spec.commit,
                    "category": category,
                    "task": prompt,
                    "visible_target": symbol.symbol_id,
                    "token_budget": _task_token_budget(spec, symbol, category),
                    "agent_profile": spec.path,
                    "expected_status": "ready",
                    "expected_target": {
                        "path": symbol.path,
                        "line": symbol.line,
                        "name": symbol.name,
                        "id": symbol.symbol_id,
                    },
                    "required_evidence": [
                        {"path": symbol.path, "line": symbol.line, "role": "target"}
                    ],
                    **(
                        {"expected_next_tool": "csegraph_graph"} if category == "structural" else {}
                    ),
                    "permitted_ranges": [{"path": symbol.path, "lines": [start, end]}],
                }
            )
    payload = {
        "schema_version": TASK_SCHEMA_VERSION_V2,
        "corpus_version": "2026.07.11-sandbox-agent-v2",
        "tier": "sandbox",
        "status": "ready",
        "repositories": repositories,
        "tasks": tasks,
    }
    return load_corpus(payload, path=Path("<generated:sandbox>"))


def _task_id(name: str, category: str, symbol: _Symbol, position: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", symbol.name.lower()).strip("-")
    return f"sandbox-{name.lower()}-{category}-{slug}-{position:03d}"


def _source_symbols(repository: Path, spec: SandboxRepositorySpec) -> list[_Symbol]:
    symbols: list[_Symbol] = []
    for root in spec.source_roots:
        base = repository / root
        if not base.exists():
            continue
        for glob in spec.source_globs:
            for path in sorted(base.rglob(glob)):
                if not path.is_file() or _ignored_path(path, repository):
                    continue
                relative = path.relative_to(repository).as_posix()
                symbols.extend(_symbols_in_file(path, relative, spec.language))
    unique: dict[tuple[str, str, str], _Symbol] = {}
    for symbol in symbols:
        # The graph ID is path + kind + name, so a later source definition is
        # the one an index lookup will retain when a name is redefined.
        unique[(symbol.path, symbol.kind, symbol.name)] = symbol
    return sorted(unique.values(), key=lambda item: (item.path, item.line, item.name))


def _ignored_path(path: Path, repository: Path) -> bool:
    return any(
        part in {".git", ".csegraph", "__pycache__", "build", "dist"}
        for part in path.relative_to(repository).parts
    )


def _symbols_in_file(path: Path, relative: str, language: str) -> list[_Symbol]:
    if language == "python":
        return _python_symbols(path, relative)
    return _script_symbols(path, relative)


def _python_symbols(path: Path, relative: str) -> list[_Symbol]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, UnicodeError):
        return []
    visitor = _PythonSymbolVisitor(relative)
    visitor.visit(tree)
    return visitor.symbols


class _PythonSymbolVisitor(ast.NodeVisitor):
    def __init__(self, relative: str) -> None:
        self.relative = relative
        self.class_depth = 0
        self.function_depth = 0
        self.symbols: list[_Symbol] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self.function_depth == 0:
            self.symbols.append(
                _Symbol(
                    path=self.relative,
                    line=_definition_line(node),
                    end_line=int(node.end_lineno or node.lineno),
                    name=node.name,
                    kind="class",
                    symbol_id=f"symbol::{self.relative}::class::{node.name}",
                )
            )
        self.class_depth += 1
        self.generic_visit(node)
        self.class_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self.function_depth == 0:
            self._record_function(node)
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if self.function_depth == 0:
            self._record_function(node)
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def visit_For(self, node: ast.For) -> None:
        return

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        return

    def visit_While(self, node: ast.While) -> None:
        return

    def visit_With(self, node: ast.With) -> None:
        return

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        return

    def visit_Match(self, node: ast.Match) -> None:
        return

    def _record_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        kind = "method" if self.class_depth else "function"
        self.symbols.append(
            _Symbol(
                path=self.relative,
                line=_definition_line(node),
                end_line=int(node.end_lineno or node.lineno),
                name=node.name,
                kind=kind,
                symbol_id=f"symbol::{self.relative}::{kind}::{node.name}",
            )
        )


_SCRIPT_SYMBOL = re.compile(
    r"^\s*(?:export\s+)?(?:(?:async)\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)"
)


def _script_symbols(path: Path, relative: str) -> list[_Symbol]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, UnicodeError):
        return []
    symbols: list[_Symbol] = []
    for line_number, line in enumerate(lines, 1):
        match = _SCRIPT_SYMBOL.match(line)
        if match is None:
            continue
        name = match.group(1)
        kind = "class" if "class" in line else "function"
        symbols.append(
            _Symbol(
                path=relative,
                line=line_number,
                end_line=line_number,
                name=name,
                kind=kind,
                symbol_id=f"symbol::{relative}::{kind}::{name}",
            )
        )
    return symbols


def _definition_line(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    decorator_lines = [int(decorator.lineno) for decorator in node.decorator_list]
    return min([int(node.lineno), *decorator_lines])


__all__ = ["TASKS_PER_TIER", "build_sandbox_corpus"]


def _task_token_budget(
    spec: SandboxRepositorySpec,
    symbol: _Symbol,
    category: str,
) -> int:
    """Give large symbols enough room for a useful target slice."""

    base = spec.token_budgets[category]
    span_lines = max(1, symbol.end_line - symbol.line + 1)
    context_room = 256 if category in {"cross-file", "debug", "structural", "test-impact"} else 0
    source_room = 256 + span_lines * 16 + context_room
    return min(16_384, max(base, source_room))
