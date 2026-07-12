"""Generate the high-N local sandbox benchmark corpus.

The stress corpus is intentionally generated from direct AST inspection of the
local sandbox repositories, not from CseGraph retrieval results. It starts with
the hand-curated sandbox release tasks for ambiguous/insufficient/structural
coverage, then adds stable exact-definition tasks for larger per-repo latency
averages.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.adaptive_benchmark import build_adaptive_corpus, corpus_to_payload

CORPUS_VERSION = "2026.07.5"
STRESS_TARGET_COUNTS = {
    "sandbox/micrograd": 20,
    "sandbox/flask": 100,
    "sandbox/django": 100,
}
BROAD_TARGET_COUNTS = {
    "sandbox/celery": 40,
    "sandbox/django": 40,
    "sandbox/fastapi": 40,
    "sandbox/flask": 40,
    "sandbox/micrograd": 20,
    "sandbox/nanoGPT": 8,
    "sandbox/pandas": 40,
    "sandbox/pytest": 40,
    "sandbox/scikit-learn": 40,
    "sandbox/transformers": 40,
}
LARGE_REPO_SOURCE_EXTRAS = 70
SMALL_REPOS = {"sandbox/micrograd", "sandbox/nanoGPT"}
TINY_DUPLICATE_TOLERANT_REPOS = {"sandbox/micrograd"}
UNSTABLE_SYMBOL_LOCATIONS = {
    ("sandbox/flask", "tests/test_apps/cliapp/factory.py", 4),
    ("sandbox/django", "django/contrib/flatpages/views.py", 22),
    ("sandbox/django", "django/contrib/gis/db/backends/postgis/base.py", 38),
}
UNSTABLE_GENERATED_PATH_PREFIXES = {
    "sandbox/django": ("django/contrib/gis/db/backends/",),
}
REPO_ALLOWED_PREFIXES = {
    "sandbox/celery": ("celery/", "t/"),
    "sandbox/django": ("django/", "tests/"),
    "sandbox/fastapi": ("fastapi/", "tests/"),
    "sandbox/flask": ("src/flask/", "tests/"),
    "sandbox/pandas": ("pandas/",),
    "sandbox/pytest": ("src/_pytest/", "testing/"),
    "sandbox/scikit-learn": ("sklearn/",),
    "sandbox/transformers": ("src/transformers/", "tests/"),
}


def main() -> int:
    generate_sandbox_corpora()
    return 0


def generate_sandbox_corpora(
    *,
    output_root: Path = REPO_ROOT / "benchmarks" / "adaptive",
) -> dict[str, Path]:
    release_payload = corpus_to_payload(build_adaptive_corpus("release", repo_root=REPO_ROOT))
    output_root.mkdir(parents=True, exist_ok=True)
    stress_output = output_root / "sandbox_stress_tasks.json"
    broad_output = output_root / "sandbox_broad_tasks.json"
    _write_corpus(
        release_payload,
        output=stress_output,
        tier="perf",
        target_counts=STRESS_TARGET_COUNTS,
        stress_profile=True,
    )
    _write_corpus(
        release_payload,
        output=broad_output,
        tier="broad",
        target_counts=BROAD_TARGET_COUNTS,
        stress_profile=False,
    )
    return {"stress": stress_output, "broad": broad_output}


def _write_corpus(
    release_payload: dict[str, Any],
    *,
    output: Path,
    tier: str,
    target_counts: dict[str, int],
    stress_profile: bool,
) -> None:
    repositories = {repo: _repository_metadata(repo, release_payload) for repo in target_counts}
    tasks: list[dict[str, Any]] = []
    for repo, target_count in target_counts.items():
        seed = [task for task in release_payload["tasks"] if task["repo"] == repo]
        commit = repositories[repo]["commit"]
        tasks.extend(seed)
        tasks.extend(
            _extra_tasks(
                repo,
                target_count,
                seed,
                commit,
                stress_profile=stress_profile,
            )
        )
    payload = {
        "schema_version": "csegraph-adaptive-benchmark-v2",
        "corpus_version": CORPUS_VERSION,
        "tier": tier,
        "status": "ready",
        "repositories": repositories,
        "tasks": tasks,
    }
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _repository_metadata(repo: str, release_payload: dict[str, Any]) -> dict[str, str]:
    metadata = release_payload["repositories"].get(repo)
    if metadata is not None:
        return dict(metadata)
    return {"url": "sandbox://local", "commit": _git_commit(REPO_ROOT / repo)}


def _git_commit(path: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        text=True,
    ).strip()


def _extra_tasks(
    repo: str,
    target_count: int,
    seed: list[dict[str, Any]],
    commit: str,
    *,
    stress_profile: bool,
) -> list[dict[str, Any]]:
    symbols, line_counts = _extract_symbols(repo)
    used = {
        (
            expected.get("path", ""),
            int(expected.get("line", 0) or 0),
        )
        for task in seed
        if (expected := task.get("expected_target"))
    }

    def eligible(symbol: dict[str, Any], *, test: bool) -> bool:
        location = (repo, symbol["path"], symbol["line"])
        if location in UNSTABLE_SYMBOL_LOCATIONS:
            return False
        if symbol["path"].startswith(UNSTABLE_GENERATED_PATH_PREFIXES.get(repo, ())):
            return False
        if not _repo_path_allowed(repo, symbol["path"]):
            return False
        if (symbol["path"], symbol["line"]) in used:
            return False
        if symbol["test"] != test:
            return False
        if symbol["decorated"]:
            return False
        if symbol["kind"] not in {"function", "method"}:
            return False
        if not 2 <= symbol["length"] <= 40:
            return False
        if symbol["leaf"].startswith("_") and repo not in TINY_DUPLICATE_TOLERANT_REPOS:
            return False
        if repo not in TINY_DUPLICATE_TOLERANT_REPOS and not symbol["unique_leaf"]:
            return False
        return not (symbol["kind"] == "method" and not symbol["unique_name"])

    needed = target_count - len(seed)
    if needed < 0:
        raise RuntimeError(f"{repo} has {len(seed)} seed tasks for target count {target_count}")
    if repo in SMALL_REPOS:
        source_count = needed
    elif stress_profile:
        source_count = min(needed, LARGE_REPO_SOURCE_EXTRAS)
    else:
        source_count = min(needed, max(1, round(needed * 0.75)))
    test_count = needed - source_count
    selected: list[dict[str, Any]] = []
    taken = set(used)
    for pool, count in (
        (_spread([symbol for symbol in symbols if eligible(symbol, test=False)]), source_count),
        (_spread([symbol for symbol in symbols if eligible(symbol, test=True)]), test_count),
    ):
        if count <= 0:
            continue
        added = 0
        for symbol in pool:
            key = (symbol["path"], symbol["line"])
            if key in taken:
                continue
            selected.append(
                _definition_task(
                    repo,
                    len(selected) + 1,
                    symbol,
                    line_counts,
                    commit,
                )
            )
            taken.add(key)
            added += 1
            if added >= count:
                break
        if added < count:
            raise RuntimeError(f"{repo} needed {count} generated tasks, got {added}")
    return selected


def _extract_symbols(repo: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    repo_root = REPO_ROOT / repo
    symbols: list[dict[str, Any]] = []
    line_counts: dict[str, int] = {}
    for path in sorted(repo_root.rglob("*.py")):
        relative = path.relative_to(repo_root).as_posix()
        if any(
            part in {".git", ".tox", ".venv", "__pycache__", "node_modules"} for part in path.parts
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        line_counts[relative] = len(text.splitlines())
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        visitor = _SymbolVisitor(relative)
        visitor.visit(tree)
        symbols.extend(visitor.symbols)
    leaf_counts = Counter(symbol["leaf"] for symbol in symbols)
    name_counts = Counter(symbol["name"] for symbol in symbols)
    for symbol in symbols:
        symbol["unique_leaf"] = leaf_counts[symbol["leaf"]] == 1
        symbol["unique_name"] = name_counts[symbol["name"]] == 1
    return symbols, line_counts


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.symbols: list[dict[str, Any]] = []
        self._class_stack: list[str] = []

    def visit_Module(self, node: ast.Module) -> None:
        for child in node.body:
            if isinstance(child, ast.ClassDef):
                self.visit_ClassDef(child)
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                self._append_symbol("function", child.name, child)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self._class_stack:
            return
        self._class_stack.append(node.name)
        for child in node.body:
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                self._append_symbol("method", f"{node.name}.{child.name}", child)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def _append_symbol(
        self,
        kind: str,
        name: str,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        end_line = getattr(node, "end_lineno", node.lineno)
        self.symbols.append(
            {
                "kind": kind,
                "name": name,
                "leaf": node.name,
                "path": self.relative_path,
                "line": node.lineno,
                "end": end_line,
                "length": end_line - node.lineno + 1,
                "test": _is_test_path(self.relative_path),
                "decorated": bool(node.decorator_list),
            }
        )


def _repo_path_allowed(repo: str, relative: str) -> bool:
    allowed = REPO_ALLOWED_PREFIXES.get(repo)
    return relative.startswith(allowed) if allowed else True


def _is_test_path(relative: str) -> bool:
    return relative.startswith(("test/", "tests/", "testing/", "t/")) or "/tests/" in relative


def _spread(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for symbol in sorted(symbols, key=lambda item: (item["path"], item["line"])):
        by_path[symbol["path"]].append(symbol)
    selected: list[dict[str, Any]] = []
    while by_path:
        for path in sorted(list(by_path)):
            candidates = by_path[path]
            if candidates:
                selected.append(candidates.pop(0))
            if not candidates:
                by_path.pop(path, None)
    return selected


def _definition_task(
    repo: str,
    index: int,
    symbol: dict[str, Any],
    line_counts: dict[str, int],
    commit: str,
) -> dict[str, Any]:
    base = repo.split("/")[-1]
    return {
        "id": f"stress-{base}-exact-{index:03d}-{_slug(symbol['name'])}",
        "repo": repo,
        "commit": commit,
        "category": "definition",
        "task": f"Explain {symbol['name']} in {symbol['path']}",
        "target": symbol["name"],
        "expected_status": "ready",
        "expected_target": {
            "path": symbol["path"],
            "line": symbol["line"],
        },
        "required_evidence": [
            {
                "path": symbol["path"],
                "line": symbol["line"],
                "role": "target",
            }
        ],
        "permitted_ranges": [
            {
                "path": symbol["path"],
                "lines": [
                    max(1, symbol["line"] - 1),
                    min(line_counts[symbol["path"]], symbol["end"] + 1),
                ],
            }
        ],
    }


def _slug(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")[:56]


if __name__ == "__main__":
    raise SystemExit(main())
