"""Flow tracing — detect entry points and trace execution flows through the call graph.

Entry points are identified by:
  - functions with no incoming calls that have outgoing calls
  - conventional names (main, run, handler, serve, cli, etc.)
  - test functions are excluded

Flow tracing starts at entry points, follows CALLS edges forward with BFS,
caps depth, and computes flow metadata: depth, node count, file count,
criticality (file spread, cross-community, test coverage gap, security
sensitivity, depth).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from csegraph._core.index.repository import ProjectIndex


_CONVENTIONAL_ENTRY_NAMES = frozenset({
    "main", "run", "cli", "entrypoint", "entry_point",
    "handler", "execute", "start", "serve", "app",
    "setup", "configure", "init", "initialize",
    "__main__", "lambda_handler", "wsgi", "asgi",
})

_SECURITY_KEYWORDS = frozenset({
    "auth", "login", "logout", "authenticate", "authorize",
    "password", "token", "secret", "encrypt", "decrypt",
    "hash", "verify", "session", "permission", "credential",
    "sanitize", "validate", "escape",
})


@dataclass
class FlowEntry:
    id: str
    name: str
    kind: str
    path: str
    line_range: Optional[List[int]] = None
    detection_reason: str = ""


@dataclass
class FlowStep:
    id: str
    name: str
    kind: str
    path: str
    depth: int
    line_range: Optional[List[int]] = None


@dataclass
class Flow:
    entry_point: FlowEntry
    steps: List[FlowStep] = field(default_factory=list)
    depth: int = 0
    node_count: int = 0
    file_count: int = 0
    criticality: float = 0.0
    criticality_factors: List[str] = field(default_factory=list)
    has_test_coverage: bool = False


@dataclass
class FlowResult:
    command: str
    db_path: str
    repo_root: str
    total_entry_points: int
    total_flows: int
    flows: List[Flow] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class FlowService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    def trace(
        self,
        *,
        entry_point: str | None = None,
        max_depth: int = 10,
        limit: int = 20,
    ) -> FlowResult:
        index = ProjectIndex(self.db_path)
        try:
            index.initialize_schema()
            metadata = index.metadata()
            repo_root = metadata["root_dir"]

            outgoing, incoming = _build_call_graph(index)
            nodes = _load_callable_nodes(index)
            tested_ids = _load_tested_ids(index)

            if entry_point:
                entries = _resolve_entry(entry_point, nodes, index, repo_root)
            else:
                entries = _detect_entries(nodes, incoming, outgoing)

            warnings: List[str] = []
            flows: List[Flow] = []

            for ep in entries[:limit]:
                flow = _trace_flow(ep, outgoing, nodes, tested_ids, max_depth)
                flows.append(flow)

            flows.sort(key=lambda f: -f.criticality)

            if len(entries) > limit:
                warnings.append(
                    f"Showing {limit} of {len(entries)} entry points. "
                    f"Use --limit to see more."
                )

            return FlowResult(
                command="flows",
                db_path=self.db_path,
                repo_root=repo_root,
                total_entry_points=len(entries),
                total_flows=len(flows),
                flows=flows,
                warnings=warnings,
            )
        finally:
            index.close()


def _build_call_graph(
    index: ProjectIndex,
) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    outgoing: Dict[str, Set[str]] = defaultdict(set)
    incoming: Dict[str, Set[str]] = defaultdict(set)
    for row in index.conn.execute(
        "SELECT source, target FROM edges WHERE relation = 'calls'"
    ):
        outgoing[row["source"]].add(row["target"])
        incoming[row["target"]].add(row["source"])
    return outgoing, incoming


def _load_callable_nodes(index: ProjectIndex) -> Dict[str, Dict[str, Any]]:
    nodes: Dict[str, Dict[str, Any]] = {}
    for row in index.conn.execute(
        "SELECT id, name, type, path, start_line, end_line, is_test, community_id "
        "FROM nodes WHERE type IN ('function', 'method', 'test')"
    ):
        nodes[row["id"]] = dict(row)
    return nodes


def _load_tested_ids(index: ProjectIndex) -> Set[str]:
    tested: Set[str] = set()
    for row in index.conn.execute(
        "SELECT DISTINCT source FROM edges WHERE relation = 'tested_by'"
    ):
        tested.add(row["source"])
    return tested


def _resolve_entry(
    name: str,
    nodes: Dict[str, Dict[str, Any]],
    index: ProjectIndex,
    repo_root: str,
) -> List[FlowEntry]:
    if name in nodes:
        return [_make_entry(nodes[name], "specified")]

    lowered = name.lower()
    for nid, n in nodes.items():
        if n["name"].lower() == lowered:
            return [_make_entry(n, "specified")]

    from csegraph._core.graph.queries import _resolve_graph_node
    try:
        resolved = _resolve_graph_node(index, name, repo_root)
        if resolved in nodes:
            return [_make_entry(nodes[resolved], "specified")]
    except ValueError:
        pass

    return []


def _detect_entries(
    nodes: Dict[str, Dict[str, Any]],
    incoming: Dict[str, Set[str]],
    outgoing: Dict[str, Set[str]],
) -> List[FlowEntry]:
    entries: List[FlowEntry] = []
    seen: Set[str] = set()

    for nid, n in nodes.items():
        if n.get("is_test"):
            continue
        if n["type"] not in ("function", "method"):
            continue

        reason = _classify_entry(nid, n, incoming, outgoing)
        if reason and nid not in seen:
            seen.add(nid)
            entries.append(_make_entry(n, reason))

    priority = {"conventional_name": 0, "no_incoming_calls": 1}
    entries.sort(key=lambda e: (priority.get(e.detection_reason, 9), e.name))
    return entries


def _classify_entry(
    nid: str,
    node: Dict[str, Any],
    incoming: Dict[str, Set[str]],
    outgoing: Dict[str, Set[str]],
) -> Optional[str]:
    name_lower = node["name"].lower()
    if name_lower in _CONVENTIONAL_ENTRY_NAMES:
        return "conventional_name"

    if nid not in incoming and nid in outgoing:
        return "no_incoming_calls"

    return None


def _make_entry(node: Dict[str, Any], reason: str) -> FlowEntry:
    start = node.get("start_line")
    end = node.get("end_line")
    lr = [int(start), int(end)] if start is not None and end is not None else None
    return FlowEntry(
        id=node["id"],
        name=node["name"],
        kind=node["type"],
        path=node.get("path", ""),
        line_range=lr,
        detection_reason=reason,
    )


def _trace_flow(
    entry: FlowEntry,
    outgoing: Dict[str, Set[str]],
    nodes: Dict[str, Dict[str, Any]],
    tested_ids: Set[str],
    max_depth: int,
) -> Flow:
    steps: List[FlowStep] = []
    visited: Set[str] = {entry.id}
    frontier: List[Tuple[str, int]] = [
        (t, 1) for t in sorted(outgoing.get(entry.id, set()))
    ]
    files: Set[str] = set()
    communities: Set[int] = set()
    untested_count = 0
    total_count = 1
    has_security = _is_security_sensitive(entry.name)
    max_reached = 0

    ep_node = nodes.get(entry.id)
    if ep_node:
        files.add(ep_node.get("path", ""))
        cid = ep_node.get("community_id")
        if cid is not None:
            communities.add(cid)
        if entry.id not in tested_ids:
            untested_count += 1

    while frontier:
        next_frontier: List[Tuple[str, int]] = []
        for target_id, depth in frontier:
            if target_id in visited or depth > max_depth:
                continue
            visited.add(target_id)

            n = nodes.get(target_id)
            if not n:
                continue
            if n.get("is_test"):
                continue

            total_count += 1
            if depth > max_reached:
                max_reached = depth

            start = n.get("start_line")
            end = n.get("end_line")
            lr = [int(start), int(end)] if start is not None and end is not None else None
            steps.append(FlowStep(
                id=target_id,
                name=n["name"],
                kind=n["type"],
                path=n.get("path", ""),
                depth=depth,
                line_range=lr,
            ))

            files.add(n.get("path", ""))
            cid = n.get("community_id")
            if cid is not None:
                communities.add(cid)
            if target_id not in tested_ids:
                untested_count += 1
            if _is_security_sensitive(n["name"]):
                has_security = True

            for next_target in sorted(outgoing.get(target_id, set())):
                if next_target not in visited:
                    next_frontier.append((next_target, depth + 1))

        frontier = next_frontier

    steps.sort(key=lambda s: (s.depth, s.name))

    criticality, factors = _compute_criticality(
        depth=max_reached,
        file_count=len(files),
        community_count=len(communities),
        untested_count=untested_count,
        total_count=total_count,
        has_security=has_security,
    )

    return Flow(
        entry_point=entry,
        steps=steps,
        depth=max_reached,
        node_count=total_count,
        file_count=len(files),
        criticality=criticality,
        criticality_factors=factors,
        has_test_coverage=entry.id in tested_ids,
    )


def _is_security_sensitive(name: str) -> bool:
    name_lower = name.lower()
    return any(kw in name_lower for kw in _SECURITY_KEYWORDS)


def _compute_criticality(
    *,
    depth: int,
    file_count: int,
    community_count: int,
    untested_count: int,
    total_count: int,
    has_security: bool,
) -> Tuple[float, List[str]]:
    score = 0.0
    factors: List[str] = []

    if file_count >= 5:
        score += 0.25
        factors.append(f"high file spread ({file_count} files)")
    elif file_count >= 3:
        score += 0.15
        factors.append(f"moderate file spread ({file_count} files)")

    if depth >= 5:
        score += 0.2
        factors.append(f"deep call chain (depth {depth})")
    elif depth >= 3:
        score += 0.1
        factors.append(f"moderate depth ({depth})")

    if community_count >= 3:
        score += 0.2
        factors.append(f"crosses {community_count} communities")
    elif community_count >= 2:
        score += 0.1
        factors.append(f"crosses {community_count} communities")

    if total_count > 0:
        untested_pct = untested_count / total_count
        if untested_pct > 0.5:
            score += 0.2
            factors.append(f"{untested_count}/{total_count} nodes untested")
        elif untested_pct > 0.2:
            score += 0.1
            factors.append(f"{untested_count}/{total_count} nodes untested")

    if has_security:
        score += 0.15
        factors.append("touches security-sensitive code")

    return round(min(score, 1.0), 2), factors
