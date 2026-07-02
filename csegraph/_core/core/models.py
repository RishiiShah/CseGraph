from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from csegraph._core.cse.metrics import SufficiencyMetrics


@dataclass(frozen=True)
class ProfileConfig:
    name: str
    top_k: int
    graph_radius: int
    context_budget: int
    raw_code_budget: int
    dep_threshold: float = 0.80
    entity_threshold: float = 0.80
    semantic_threshold: float = 0.50
    semantic_threshold_relaxed: float = 0.03
    confidence_threshold: float = 0.70
    max_bytes: Optional[int] = None


@dataclass
class IndexResult:
    command: str
    db_path: str
    repo_root: str
    profile: str
    files_indexed: int
    symbols_indexed: int
    edges_indexed: int
    cache_hits: int = 0
    cache_misses: int = 0
    unchanged_files: List[str] = field(default_factory=list)
    changed_files: List[str] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)
    parse_errors: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    timings_ms: Dict[str, float] = field(default_factory=dict)
    postprocess_level: str = "none"
    postprocess: Dict[str, Any] = field(default_factory=dict)
    postprocess_skipped_reason: Optional[str] = None
    graph_totals: Dict[str, int] = field(default_factory=dict)


@dataclass
class RefreshResult:
    command: str
    db_path: str
    repo_root: str
    profile: str
    files_indexed: int
    symbols_indexed: int
    edges_indexed: int
    cache_hits: int = 0
    cache_misses: int = 0
    unchanged_files: List[str] = field(default_factory=list)
    changed_files: List[str] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)
    changed_symbols: List[str] = field(default_factory=list)
    parse_errors: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    dependents_expanded: int = 0
    dependents_cap_hit: bool = False
    timings_ms: Dict[str, float] = field(default_factory=dict)
    postprocess_level: str = "none"
    postprocess: Dict[str, Any] = field(default_factory=dict)
    postprocess_skipped_reason: Optional[str] = None
    graph_totals: Dict[str, int] = field(default_factory=dict)


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
    reason_details: List[Dict[str, Any]] = field(default_factory=list)
    explanation: Optional[str] = None
    source_omitted_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.language, str) or not self.language:
            raise ValueError("ContextNode.language must be a non-empty string")


@dataclass
class RelationshipOccurrence:
    path: str
    line_range: Optional[List[int]] = None
    enclosing_symbol_id: Optional[str] = None
    name: Optional[str] = None
    kind: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    snippet: Optional[str] = None


@dataclass
class ContextRelationship:
    source: str
    target: str
    relation: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    occurrences: List[RelationshipOccurrence] = field(default_factory=list)
    confidence: float = 1.0
    confidence_tier: str = "EXTRACTED"
    source_path: Optional[str] = None
    target_path: Optional[str] = None


@dataclass
class ImportPrelude:
    path: str
    language: str
    text: str
    line_range: Optional[List[int]]
    source_node_ids: List[str] = field(default_factory=list)
    resolved_imports: List[str] = field(default_factory=list)


@dataclass
class SufficiencyResult:
    sufficient: bool
    metrics: SufficiencyMetrics
    thresholds: Dict[str, float] = field(default_factory=dict)
    failure_reasons: List[Dict[str, Any]] = field(default_factory=list)
    recovery: List[Dict[str, Any]] = field(default_factory=list)
    edit_ready: bool = False


@dataclass
class ContextResult:
    command: str
    db_path: str
    repo_root: str
    profile: str
    query: str
    target: str
    detail_level: str
    returned_detail_level: str
    sufficiency: SufficiencyResult
    total_estimated_tokens: int
    nodes: List[ContextNode]
    indexed_corpus_bytes: int = 0
    indexed_corpus_estimated_tokens: int = 0
    relationships: List[ContextRelationship] = field(default_factory=list)
    import_preludes: List[ImportPrelude] = field(default_factory=list)
    target_input: Optional[str] = None
    source_policy: str = "auto"
    raw_code_nodes: List[str] = field(default_factory=list)
    next_actions: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    run_id: Optional[int] = None
    confidence_breakdown: Dict[str, int] = field(default_factory=dict)
    timings_ms: Dict[str, float] = field(default_factory=dict)
    target_resolution: str = "resolved"
    target_candidates: List[Dict[str, Any]] = field(default_factory=list)
    target_confidence: Optional[float] = None
    target_score_margin: Optional[float] = None
    task_kind: str = "auto"
    intent: str = "understand"
    edit_targets: List[Dict[str, Any]] = field(default_factory=list)
    impact: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    affected_tests: List[Dict[str, Any]] = field(default_factory=list)
    missing_context: List[Dict[str, Any]] = field(default_factory=list)


class ContextStatus(str, Enum):
    READY = "ready"
    AMBIGUOUS = "ambiguous"
    INSUFFICIENT = "insufficient"
    INDEX_REQUIRED = "index_required"
    REFRESH_REQUIRED = "refresh_required"


@dataclass(frozen=True)
class ContextRequest:
    task: str
    repo: Optional[str] = None
    target: Optional[str] = None
    task_kind: str = "auto"
    token_budget: int = 800
    encoding: str = "o200k_base"
    include_source: str = "auto"
    response_mode: str = "compact"
    engine: str = "adaptive"
    cursor: Optional[str] = None
    max_bytes: Optional[int] = None


@dataclass
class ContextTarget:
    id: str
    name: str
    kind: str
    path: str
    lines: Optional[List[int]]
    confidence: float


@dataclass
class ContextSlice:
    path: str
    lines: Optional[List[int]]
    symbol: str
    role: str
    code: str
    id: Optional[str] = None


@dataclass
class ContextResponse:
    schema_version: str
    status: ContextStatus
    intent: str
    target: Optional[ContextTarget]
    slices: List[ContextSlice]
    freshness: Dict[str, Any]
    usage: Dict[str, Any]
    cursor: Optional[str] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    missing: List[Dict[str, Any]] = field(default_factory=list)
    next: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)
    diagnostic: Optional[Dict[str, Any]] = None


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
    detail_level: str = "standard"
    summary: str = ""
    total_nodes: int = 0
    total_edges: int = 0
    truncated: bool = False
    hubs_skipped: int = 0
    relations_filter: List[str] = field(default_factory=list)
    confidence_breakdown: Dict[str, int] = field(default_factory=dict)


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
    detail_level: str = "standard"
    summary: str = ""
    relations_filter: List[str] = field(default_factory=list)
    hubs_skipped: int = 0
    confidence_breakdown: Dict[str, int] = field(default_factory=dict)


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
class BenchmarkCorpusTask:
    id: str
    query: str
    target: Optional[str] = None
    include_source: str = "never"
    detail_level: str = "auto"
    max_tokens: Optional[int] = None
    expected_nodes: List[str] = field(default_factory=list)
    expected_files: List[str] = field(default_factory=list)
    expected_symbols: List[str] = field(default_factory=list)
    expected_relationships: List[Dict[str, str]] = field(default_factory=list)
    expected_occurrence_snippets: List[str] = field(default_factory=list)
    expected_import_preludes: List[str] = field(default_factory=list)
    forbidden_source_patterns: List[str] = field(default_factory=list)


@dataclass
class BenchmarkCorpusTaskResult:
    task_id: str
    query: str
    target: Optional[str]
    returned_target: Optional[str]
    returned_detail_level: Optional[str]
    sufficient: bool
    returned_node_count: int
    context_tokens: int
    response_bytes: int
    tool_call_count: int
    hit_rate: float
    node_hit_rate: float
    file_hit_rate: float
    symbol_hit_rate: float
    relationship_hit_rate: float
    occurrence_snippet_hit_rate: float
    import_prelude_hit_rate: float
    forbidden_source_pattern_hit_rate: float
    expected_node_total: int
    expected_file_total: int
    expected_symbol_total: int
    expected_relationship_total: int
    expected_occurrence_snippet_total: int
    expected_import_prelude_total: int
    forbidden_source_pattern_total: int
    expected_hit_count: int
    expected_total: int
    missing_expected_nodes: List[str] = field(default_factory=list)
    missing_expected_files: List[str] = field(default_factory=list)
    missing_expected_symbols: List[str] = field(default_factory=list)
    missing_expected_relationships: List[str] = field(default_factory=list)
    missing_expected_occurrence_snippets: List[str] = field(default_factory=list)
    missing_expected_import_preludes: List[str] = field(default_factory=list)
    violating_forbidden_source_patterns: List[str] = field(default_factory=list)
    error: Optional[str] = None
    target_resolution: Optional[str] = None
    target_confidence: Optional[float] = None
    sufficiency_failure_count: int = 0
    recovery_action_count: int = 0
    relationship_occurrence_count: int = 0
    duplicate_occurrence_count: int = 0


@dataclass
class BenchmarkCorpusSummary:
    task_count: int
    passed_task_count: int
    failed_task_count: int
    overall_hit_rate: float
    task_pass_rate: float
    sufficient_task_count: int
    total_context_tokens: int
    avg_context_tokens: float
    total_response_bytes: int
    avg_response_bytes: float
    total_tool_call_count: int


@dataclass
class BenchmarkCorpusResult:
    command: str
    db_path: str
    repo_root: str
    profile: str
    corpus_path: str
    total_elapsed_ms: float
    index_stats: Dict[str, Any]
    summary: BenchmarkCorpusSummary
    tasks: List[BenchmarkCorpusTaskResult] = field(default_factory=list)


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
class IndexHealth:
    verdict: str
    summary: str
    index_age_hours: Optional[float] = None
    metrics: Dict[str, int] = field(default_factory=dict)
    hints: List[str] = field(default_factory=list)


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
    index_health: Optional[IndexHealth] = None
    local_context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PostprocessResult:
    command: str
    db_path: str
    repo_root: str
    fts_entries: int
    communities_detected: int
    modularity: float = 0.0
    skipped: List[str] = field(default_factory=list)
    resolvers_edges_added: int = 0
    level: str = "full"
    timings_ms: Dict[str, float] = field(default_factory=dict)


@dataclass
class McpInstallTarget:
    platform: str
    path: str
    scope: str
    action: str
    dry_run: bool = False
    reason: Optional[str] = None


@dataclass
class McpInstallResult:
    command: str
    repo_root: str
    server_name: str
    server_command: str
    server_args: List[str]
    dry_run: bool
    installed: List[McpInstallTarget] = field(default_factory=list)
    skipped: List[McpInstallTarget] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)
    verification: Dict[str, Any] = field(default_factory=dict)


@dataclass
class McpDoctorResult:
    command: str
    repo_root: str
    platform: str
    state: str
    config_path: Optional[str] = None
    config_present: bool = False
    launcher_present: bool = False
    contract_valid: bool = False
    contract_issues: List[str] = field(default_factory=list)
    protocol_verified: bool = False
    observed_call: bool = False
    require_observed_call: bool = False
    server_entry: Dict[str, Any] = field(default_factory=dict)
    verification: Dict[str, Any] = field(default_factory=dict)
    host_verification: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


@dataclass
class McpDoctorAggregateResult:
    command: str
    repo_root: str
    platform: str
    state: str
    configured_count: int = 0
    missing_count: int = 0
    launcher_missing_count: int = 0
    contract_invalid_count: int = 0
    protocol_verified_count: int = 0
    observed_call_count: int = 0
    require_observed_call: bool = False
    platforms: List[McpDoctorResult] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


@dataclass
class KeyEntity:
    id: str
    name: str
    kind: str
    path: str
    degree: int


@dataclass
class NextToolSuggestion:
    tool: str
    reason: str
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MinimalResult:
    command: str
    db_path: str
    repo_root: str
    summary: str
    task: Optional[str]
    task_intent: str
    key_entities: List[KeyEntity]
    next_tool_suggestions: List[NextToolSuggestion]
    estimated_tokens: int
    index_health: Optional[IndexHealth] = None
    suggested_queries: List[str] = field(default_factory=list)


@dataclass
class ExportResult:
    command: str
    db_path: str
    repo_root: str
    output_path: str
    format: str
    total_nodes: int
    total_edges: int
    files_written: int


@dataclass
class CommunitySummary:
    community_id: int
    label: str
    size: int
    files: int
    languages: Dict[str, int] = field(default_factory=dict)
    type_counts: Dict[str, int] = field(default_factory=dict)
    key_symbols: List[Dict[str, Any]] = field(default_factory=list)
    internal_edges: int = 0
    cross_edges: int = 0
    test_count: int = 0


@dataclass
class CouplingPair:
    community_a: int
    community_b: int
    label_a: str
    label_b: str
    weight: int
    relations: Dict[str, int] = field(default_factory=dict)


@dataclass
class ArchitectureResult:
    command: str
    db_path: str
    repo_root: str
    total_nodes: int
    total_edges: int
    num_communities: int
    summaries: List["CommunitySummary"] = field(default_factory=list)
    coupling: List["CouplingPair"] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class RegistryEntry:
    alias: str
    root: str
    db: str
    profile: str = "medium"
    added_at: Optional[str] = None


@dataclass
class RegistryResult:
    command: str
    entries: List["RegistryEntry"] = field(default_factory=list)
    action: Optional[str] = None
    alias: Optional[str] = None
    message: Optional[str] = None


@dataclass
class DaemonEntry:
    alias: str
    root: str
    pid: Optional[int] = None
    status: str = "stopped"
    last_refresh: Optional[str] = None
    error: Optional[str] = None


@dataclass
class DaemonResult:
    command: str
    running: bool = False
    entries: List["DaemonEntry"] = field(default_factory=list)
    pid_file: Optional[str] = None
    message: Optional[str] = None


@dataclass
class EmbeddingSearchHit:
    node_id: str
    name: str
    kind: str
    path: str
    score: float
    source: str = "embedding"


@dataclass
class EmbeddingResult:
    command: str
    db_path: str
    repo_root: str
    action: str
    model: str
    provider: str
    nodes_embedded: int = 0
    nodes_skipped: int = 0
    nodes_cached: int = 0
    query: str = ""
    top_k: int = 0
    hits: List["EmbeddingSearchHit"] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def to_dict(value: Any) -> Any:
    from csegraph._core.core.serializer import to_dict as serialize

    return serialize(value)
