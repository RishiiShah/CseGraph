"""Postprocess resolver passes that add INFERRED edges after all files are parsed.

Each resolver reads existing nodes/edges, discovers missing relationships, and
inserts new edges with confidence_tier='INFERRED'. Resolvers are idempotent —
running them multiple times produces the same result because edges use
INSERT OR IGNORE with the UNIQUE constraint.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from csegraph._core.index.repository import ProjectIndex


@dataclass
class ResolverStats:
    name: str
    edges_added: int = 0
    edges_skipped: int = 0


@dataclass
class ResolverResult:
    command: str
    db_path: str
    repo_root: str
    resolvers_run: List[ResolverStats] = field(default_factory=list)
    total_edges_added: int = 0
    warnings: List[str] = field(default_factory=list)


class ResolverService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def run_all(self) -> ResolverResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]

            stats: List[ResolverStats] = []
            warnings: List[str] = []

            stats.append(_resolve_transitive_test_edges(index))
            stats.append(_resolve_python_imports(index, repo_root))
            stats.append(_resolve_ts_aliases(index, repo_root))

            index.conn.commit()

            total = sum(s.edges_added for s in stats)
            return ResolverResult(
                command="resolvers",
                db_path=self.db_path,
                repo_root=repo_root,
                resolvers_run=stats,
                total_edges_added=total,
                warnings=warnings,
            )
        finally:
            index.close()


# -- Transitive test edges -----------------------------------------------------


def _resolve_transitive_test_edges(index: ProjectIndex, max_depth: int = 3) -> ResolverStats:
    """Walk call chains from test functions to discover indirect tested_by edges.

    If test_func calls helper and helper calls prod_func, then prod_func gets
    a tested_by edge to test_func (depth=2).
    """
    stats = ResolverStats(name="transitive_test_edges")

    test_ids: Set[str] = set()
    for row in index.conn.execute(
        "SELECT id FROM nodes WHERE is_test = 1 AND type IN ('function', 'method', 'test')"
    ):
        test_ids.add(row["id"])

    if not test_ids:
        return stats

    outgoing_calls: Dict[str, Set[str]] = defaultdict(set)
    for row in index.conn.execute("SELECT source, target FROM edges WHERE relation = 'calls'"):
        outgoing_calls[row["source"]].add(row["target"])

    existing_tested_by: Set[Tuple[str, str]] = set()
    for row in index.conn.execute("SELECT source, target FROM edges WHERE relation = 'tested_by'"):
        existing_tested_by.add((row["source"], row["target"]))

    new_edges: List[Tuple[str, str, str, str, float, str]] = []

    for test_id in test_ids:
        visited: Set[str] = {test_id}
        frontier = outgoing_calls.get(test_id, set()).copy()
        depth = 1

        while frontier and depth <= max_depth:
            next_frontier: Set[str] = set()
            for target_id in frontier:
                if target_id in visited or target_id in test_ids:
                    continue
                visited.add(target_id)

                if (target_id, test_id) not in existing_tested_by:
                    existing_tested_by.add((target_id, test_id))
                    meta = json.dumps({"via": "transitive", "depth": depth})
                    new_edges.append(
                        (
                            target_id,
                            test_id,
                            "tested_by",
                            meta,
                            round(0.8 / depth, 2),
                            "INFERRED",
                        )
                    )
                    stats.edges_added += 1

                next_frontier.update(outgoing_calls.get(target_id, set()))
            frontier = next_frontier
            depth += 1

    if new_edges:
        index.conn.executemany(
            "INSERT OR IGNORE INTO edges(source, target, relation, metadata, confidence, confidence_tier) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            new_edges,
        )

    return stats


# -- Python import resolver ----------------------------------------------------


def _resolve_python_imports(index: ProjectIndex, repo_root: str) -> ResolverStats:
    """Retry unresolved Python imports using __init__.py and suffix matching.

    Finds file nodes that have no outgoing 'imports' edge but whose source
    contains import statements, and tries additional resolution strategies.
    """
    stats = ResolverStats(name="python_import_resolver")

    py_files: Dict[str, str] = {}
    for row in index.conn.execute(
        "SELECT id, path FROM nodes WHERE type = 'file' AND language = 'python'"
    ):
        py_files[row["id"]] = row["path"]

    if not py_files:
        return stats

    module_map = _build_python_module_map(py_files)

    existing_imports: Set[Tuple[str, str]] = set()
    for row in index.conn.execute("SELECT source, target FROM edges WHERE relation = 'imports'"):
        existing_imports.add((row["source"], row["target"]))

    symbol_names: Dict[str, List[str]] = defaultdict(list)
    for row in index.conn.execute(
        "SELECT id, name, path FROM nodes WHERE type IN ('class', 'function', 'method') AND language = 'python'"
    ):
        symbol_names[row["name"]].append(row["id"])

    new_edges: List[Tuple[str, str, str, str, float, str]] = []

    for file_id, file_path in py_files.items():
        source_path = Path(repo_root) / file_path
        if not source_path.exists():
            continue

        try:
            content = source_path.read_text(errors="replace")
        except OSError:
            continue

        for line in content.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue

            import_name = _extract_import_module(stripped)
            if not import_name:
                continue

            resolved = _try_python_resolve(import_name, file_path, module_map, py_files)
            if resolved and (file_id, resolved) not in existing_imports:
                existing_imports.add((file_id, resolved))
                meta = json.dumps({"import": import_name, "strategy": "fallback"})
                new_edges.append(
                    (
                        file_id,
                        resolved,
                        "imports",
                        meta,
                        0.7,
                        "INFERRED",
                    )
                )
                stats.edges_added += 1

    if new_edges:
        index.conn.executemany(
            "INSERT OR IGNORE INTO edges(source, target, relation, metadata, confidence, confidence_tier) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            new_edges,
        )

    return stats


def _build_python_module_map(py_files: Dict[str, str]) -> Dict[str, str]:
    module_map: Dict[str, str] = {}
    for file_id, path in py_files.items():
        normalized = path.replace("\\", "/")
        if normalized.endswith("/__init__.py"):
            module = normalized[:-12].replace("/", ".")
        elif normalized.endswith(".py"):
            module = normalized[:-3].replace("/", ".")
        else:
            continue
        module_map[module] = file_id
    return module_map


def _extract_import_module(line: str) -> Optional[str]:
    if line.startswith("from "):
        parts = line.split()
        if len(parts) >= 2:
            return parts[1]
    elif line.startswith("import "):
        parts = line.split()
        if len(parts) >= 2:
            mod = parts[1].rstrip(",")
            return mod
    return None


def _try_python_resolve(
    import_name: str,
    current_path: str,
    module_map: Dict[str, str],
    py_files: Dict[str, str],
) -> Optional[str]:
    if import_name.startswith("."):
        return _resolve_relative_python(import_name, current_path, module_map)

    if import_name in module_map:
        return module_map[import_name]

    parts = import_name.split(".")
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in module_map:
            return module_map[prefix]

    return None


def _resolve_relative_python(
    import_name: str,
    current_path: str,
    module_map: Dict[str, str],
) -> Optional[str]:
    normalized = current_path.replace("\\", "/")
    dots = len(import_name) - len(import_name.lstrip("."))
    suffix = import_name[dots:]

    if normalized.endswith("/__init__.py"):
        parts = normalized[:-12].split("/")
    elif normalized.endswith(".py"):
        parts = normalized[:-3].split("/")
    else:
        return None

    base = parts[: max(0, len(parts) - dots + 1)]
    if suffix:
        target_module = ".".join(base + suffix.split("."))
    else:
        target_module = ".".join(base)

    return module_map.get(target_module)


# -- TypeScript alias resolver -------------------------------------------------


def _resolve_ts_aliases(index: ProjectIndex, repo_root: str) -> ResolverStats:
    """Read tsconfig.json/jsconfig.json paths and re-resolve aliased imports."""
    stats = ResolverStats(name="ts_alias_resolver")

    aliases = _load_ts_path_aliases(repo_root)
    if not aliases:
        return stats

    ts_files: Dict[str, str] = {}
    for row in index.conn.execute(
        "SELECT id, path FROM nodes WHERE type = 'file' AND language IN ('typescript', 'javascript', 'tsx', 'jsx')"
    ):
        ts_files[row["id"]] = row["path"]

    if not ts_files:
        return stats

    file_by_path: Dict[str, str] = {}
    for file_id, path in ts_files.items():
        file_by_path[path.replace("\\", "/")] = file_id

    existing_imports: Set[Tuple[str, str]] = set()
    for row in index.conn.execute("SELECT source, target FROM edges WHERE relation = 'imports'"):
        existing_imports.add((row["source"], row["target"]))

    new_edges: List[Tuple[str, str, str, str, float, str]] = []

    for file_id, file_path in ts_files.items():
        source_path = Path(repo_root) / file_path
        if not source_path.exists():
            continue
        try:
            content = source_path.read_text(errors="replace")
        except OSError:
            continue

        for line in content.splitlines():
            import_path = _extract_ts_import_path(line)
            if not import_path:
                continue
            if import_path.startswith(".") or import_path.startswith("/"):
                continue

            resolved_path = _resolve_alias(import_path, aliases, repo_root, file_by_path)
            if resolved_path and (file_id, resolved_path) not in existing_imports:
                existing_imports.add((file_id, resolved_path))
                meta = json.dumps({"import": import_path, "strategy": "tsconfig_alias"})
                new_edges.append(
                    (
                        file_id,
                        resolved_path,
                        "imports",
                        meta,
                        0.7,
                        "INFERRED",
                    )
                )
                stats.edges_added += 1

    if new_edges:
        index.conn.executemany(
            "INSERT OR IGNORE INTO edges(source, target, relation, metadata, confidence, confidence_tier) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            new_edges,
        )

    return stats


def _load_ts_path_aliases(repo_root: str) -> Dict[str, List[str]]:
    """Load path aliases from tsconfig.json or jsconfig.json."""
    aliases: Dict[str, List[str]] = {}
    for config_name in ("tsconfig.json", "jsconfig.json"):
        config_path = Path(repo_root) / config_name
        if not config_path.exists():
            continue
        try:
            raw = config_path.read_text(errors="replace")
            data = json.loads(raw)
            paths = data.get("compilerOptions", {}).get("paths", {})
            base_url = data.get("compilerOptions", {}).get("baseUrl", ".")
            for alias, targets in paths.items():
                resolved_targets = []
                for t in targets:
                    resolved_targets.append((Path(base_url) / t).as_posix())
                aliases[alias] = resolved_targets
            if aliases:
                break
        except (json.JSONDecodeError, OSError):
            continue
    return aliases


def _extract_ts_import_path(line: str) -> Optional[str]:
    stripped = line.strip()
    for pattern in ("from '", 'from "', "from '", 'from "'):
        idx = stripped.find(pattern)
        if idx >= 0:
            rest = stripped[idx + len(pattern) :]
            end = rest.find("'" if pattern.endswith("'") else '"')
            if end > 0:
                return rest[:end]
    for pattern in ("require('", 'require("'):
        idx = stripped.find(pattern)
        if idx >= 0:
            rest = stripped[idx + len(pattern) :]
            end = rest.find("'" if pattern.endswith("'") else '"')
            if end > 0:
                return rest[:end]
    return None


def _resolve_alias(
    import_path: str,
    aliases: Dict[str, List[str]],
    repo_root: str,
    file_by_path: Dict[str, str],
) -> Optional[str]:
    for alias_pattern, targets in aliases.items():
        if alias_pattern.endswith("/*"):
            prefix = alias_pattern[:-2]
            if import_path.startswith(prefix + "/"):
                remainder = import_path[len(prefix) + 1 :]
                for target in targets:
                    if target.endswith("/*"):
                        base = target[:-2]
                    else:
                        base = target
                    candidate = f"{base}/{remainder}"
                    resolved = _probe_ts_file(candidate, repo_root, file_by_path)
                    if resolved:
                        return resolved
        else:
            if import_path == alias_pattern:
                for target in targets:
                    resolved = _probe_ts_file(target, repo_root, file_by_path)
                    if resolved:
                        return resolved
    return None


_TS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")


def _probe_ts_file(
    candidate: str,
    repo_root: str,
    file_by_path: Dict[str, str],
) -> Optional[str]:
    normalized = candidate.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized in file_by_path:
        return file_by_path[normalized]

    for ext in _TS_EXTENSIONS:
        probed = normalized + ext
        if probed in file_by_path:
            return file_by_path[probed]

    for ext in _TS_EXTENSIONS:
        probed = f"{normalized}/index{ext}"
        if probed in file_by_path:
            return file_by_path[probed]

    return None
