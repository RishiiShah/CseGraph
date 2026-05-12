"""csegraph v1.4.0 SDK.

Thin facade over `csegraph-core` (import namespace: `csegraph_core`) for
coding-agent context retrieval. The CLI package (`csegraph-cli`) imports
from `csegraph-core` directly and does not depend on this package.
"""
from __future__ import annotations

from csegraph_core import (
    ContextNode,
    ContextResult,
    ContextService,
    GraphEdgeView,
    GraphNodeView,
    GraphQueryService,
    GraphResult,
    IndexResult,
    IndexService,
    PROFILES,
    ProfileConfig,
    ProjectIndex,
    RefreshResult,
    RefreshService,
    ReportResult,
    ReportService,
    SufficiencyMetrics,
    SufficiencyResult,
    VALID_REASONS,
    VisualExportResult,
    VisualExportService,
    get_profile,
    load_profile,
    to_dict,
)

__version__ = "1.4.0"

__all__ = [
    "__version__",
    "ContextNode",
    "ContextResult",
    "ContextService",
    "GraphEdgeView",
    "GraphNodeView",
    "GraphQueryService",
    "GraphResult",
    "IndexResult",
    "IndexService",
    "PROFILES",
    "ProfileConfig",
    "ProjectIndex",
    "RefreshResult",
    "RefreshService",
    "ReportResult",
    "ReportService",
    "SufficiencyMetrics",
    "SufficiencyResult",
    "VALID_REASONS",
    "VisualExportResult",
    "VisualExportService",
    "get_profile",
    "load_profile",
    "to_dict",
]
