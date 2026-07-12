from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


@dataclass
class IndexResult:
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


@dataclass
class RefreshResult:
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
    source_mode: str = "auto"
    diagnostic: bool = False


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
    slices: List[ContextSlice]
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    missing: List[Dict[str, Any]] = field(default_factory=list)
    next: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)
    diagnostics: Optional[Dict[str, Any]] = None


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
    confidence: float = 1.0
    confidence_tier: str = "EXTRACTED"


@dataclass
class GraphResult:
    target: str
    nodes: List[GraphNodeView]
    edges: List[GraphEdgeView]
    depth: int = 1
    summary: str = ""
    total_nodes: int = 0
    total_edges: int = 0
    truncated: bool = False


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
    source: str
    target: str
    found: bool
    length: int
    nodes: List[PathStep]
    edges: List[PathEdge]
    summary: str = ""


@dataclass
class StatusResult:
    schema_version: str
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
    summary: str
    key_entities: List[KeyEntity]
    next_tool_suggestions: List[NextToolSuggestion]
