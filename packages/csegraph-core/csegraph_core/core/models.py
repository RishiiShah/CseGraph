from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List, Optional

from csegraph_core.cse.metrics import SufficiencyMetrics


@dataclass(frozen=True)
class ProfileConfig:
    name: str
    top_k: int
    graph_radius: int
    context_budget: int
    import_budget: int
    raw_code_budget: int


@dataclass
class IndexResult:
    command: str
    db_path: str
    repo_root: str
    profile: str
    files_indexed: int
    symbols_indexed: int
    edges_indexed: int
    unchanged_files: List[str] = field(default_factory=list)
    changed_files: List[str] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)
    parse_errors: Dict[str, str] = field(default_factory=dict)


@dataclass
class RefreshResult:
    command: str
    db_path: str
    repo_root: str
    profile: str
    files_indexed: int
    symbols_indexed: int
    edges_indexed: int
    unchanged_files: List[str] = field(default_factory=list)
    changed_files: List[str] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)
    changed_symbols: List[str] = field(default_factory=list)
    parse_errors: Dict[str, str] = field(default_factory=dict)


@dataclass
class ContextNode:
    node_id: str
    kind: str
    name: str
    file_path: str
    start_line: Optional[int]
    end_line: Optional[int]
    score: float
    raw_code: bool = False
    evidence: List[str] = field(default_factory=list)
    summary: str = ""
    lineage: List[str] = field(default_factory=list)


@dataclass
class ContextResult:
    command: str
    db_path: str
    repo_root: str
    profile: str
    task: str
    target_node_id: str
    is_sufficient: bool
    metrics: SufficiencyMetrics
    context_nodes: List[ContextNode]
    raw_code_nodes: List[str] = field(default_factory=list)
    thresholds: Dict[str, float] = field(default_factory=dict)
    run_id: Optional[int] = None


@dataclass
class GraphNodeView:
    node_id: str
    kind: str
    name: str
    file_path: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None


@dataclass
class GraphEdgeView:
    source: str
    target: str
    relation: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphResult:
    command: str
    db_path: str
    repo_root: str
    node_id: str
    depth: int
    nodes: List[GraphNodeView]
    edges: List[GraphEdgeView]


@dataclass
class CodegenResult:
    command: str
    db_path: str
    repo_root: str
    profile: str
    task: str
    target_node_id: str
    model: str
    generated_code: str
    is_sufficient: bool
    metrics: SufficiencyMetrics
    context_nodes_used: List[str] = field(default_factory=list)
    raw_code_nodes_used: List[str] = field(default_factory=list)
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    elapsed_seconds: Optional[float] = None
    output_path: Optional[str] = None


def to_dict(value: Any) -> Any:
    if is_dataclass(value):
        return {key: to_dict(item) for key, item in asdict(value).items()}
    if isinstance(value, list):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_dict(item) for key, item in value.items()}
    return value
