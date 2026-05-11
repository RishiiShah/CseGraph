from __future__ import annotations

from dataclasses import dataclass, field
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
    dep_threshold: float = 0.80
    entity_threshold: float = 0.80
    semantic_threshold: float = 0.50
    semantic_threshold_relaxed: float = 0.0
    confidence_threshold: float = 0.70
    max_expansion_rounds: int = 3
    tier0_target: float = 1.00
    tier1_target: float = 0.75
    confidence_drop_threshold: float = 0.15
    compression_hub_count: int = 20
    compression_max_nodes_per_slice: int = 50
    compression_source_char_limit: int = 800


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
    language: str
    raw_code: bool = False
    evidence: List[str] = field(default_factory=list)
    summary: str = ""
    lineage: List[str] = field(default_factory=list)
    source_text: Optional[str] = None
    estimated_tokens: int = 0
    reason: List[str] = field(default_factory=list)
    explanation: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.language, str) or not self.language:
            raise ValueError("ContextNode.language must be a non-empty string")


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
    estimated_tokens: int = 0


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
class VisualExportResult:
    command: str
    db_path: str
    repo_root: str
    output_path: str
    total_nodes: int
    total_edges: int


@dataclass
class ReportResult:
    command: str
    db_path: str
    repo_root: str
    total_files: int
    total_symbols: int
    total_edges: int
    parse_error_count: int
    node_counts: Dict[str, int] = field(default_factory=dict)
    edge_counts: Dict[str, int] = field(default_factory=dict)
    god_nodes: List[Dict[str, Any]] = field(default_factory=list)
    knowledge_gaps: List[Dict[str, Any]] = field(default_factory=list)
    surprising_connections: List[Dict[str, Any]] = field(default_factory=list)
    suggested_questions: List[str] = field(default_factory=list)


def to_dict(value: Any) -> Any:
    from csegraph_core.core.serializer import to_dict as serialize

    return serialize(value)
