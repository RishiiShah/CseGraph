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
    timings_ms: Dict[str, float] = field(default_factory=dict)


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
    id: str
    kind: str
    name: str
    path: str
    line_range: Optional[List[int]]
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
class SufficiencyResult:
    sufficient: bool
    metrics: SufficiencyMetrics
    thresholds: Dict[str, float] = field(default_factory=dict)


@dataclass
class ContextResult:
    command: str
    db_path: str
    repo_root: str
    profile: str
    query: str
    target: str
    sufficiency: SufficiencyResult
    total_estimated_tokens: int
    nodes: List[ContextNode]
    raw_code_nodes: List[str] = field(default_factory=list)
    run_id: Optional[int] = None


@dataclass
class GraphNodeView:
    id: str
    kind: str
    name: str
    path: str
    line_range: Optional[List[int]] = None


@dataclass
class GraphEdgeView:
    source: str
    target: str
    relation: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    confidence_tier: str = "EXTRACTED"


@dataclass
class GraphResult:
    command: str
    db_path: str
    repo_root: str
    target: str
    depth: int
    nodes: List[GraphNodeView]
    edges: List[GraphEdgeView]


@dataclass
class PathStep:
    node_id: str
    kind: str
    name: str
    path: str
    line_range: Optional[List[int]] = None


@dataclass
class PathEdge:
    source: str
    target: str
    relation: str


@dataclass
class PathResult:
    command: str
    db_path: str
    repo_root: str
    source: str
    target: str
    found: bool
    length: int
    nodes: List[PathStep]
    edges: List[PathEdge]


@dataclass
class VisualExportResult:
    command: str
    db_path: str
    repo_root: str
    output_path: str
    total_nodes: int
    total_edges: int


@dataclass
class BenchmarkStep:
    name: str
    elapsed_ms: float
    stats: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkResult:
    command: str
    db_path: str
    repo_root: str
    profile: str
    query: str
    target: Optional[str]
    graph_output_path: str
    total_elapsed_ms: float
    steps: List[BenchmarkStep] = field(default_factory=list)


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
    knowledge_gap_groups: List[Dict[str, Any]] = field(default_factory=list)
    surprising_connections: List[Dict[str, Any]] = field(default_factory=list)
    suggested_questions: List[str] = field(default_factory=list)
    sections: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class StatusResult:
    command: str
    db_path: str
    repo_root: str
    schema_version: str
    active_profile: str
    total_nodes: int
    total_edges: int
    total_files: int
    languages: List[str]
    parse_error_count: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    built_branch: Optional[str] = None
    built_commit: Optional[str] = None
    current_branch: Optional[str] = None
    current_commit: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    parse_errors: Dict[str, str] = field(default_factory=dict)


@dataclass
class PostprocessResult:
    command: str
    db_path: str
    repo_root: str
    fts_entries: int
    communities_detected: int
    modularity: float = 0.0
    skipped: List[str] = field(default_factory=list)


def to_dict(value: Any) -> Any:
    from csegraph_core.core.serializer import to_dict as serialize

    return serialize(value)
