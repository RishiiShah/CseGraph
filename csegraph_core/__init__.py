"""csegraph_core - import namespace for the csegraph-core distribution.

The install/distribution name is `csegraph-core`; Python imports use
`csegraph_core` because hyphens are invalid in module names.

This package is the source-of-truth context engine for coding agents:
parser, SQLite index, graph traversal, retrieval, and CSE metrics. The SDK
package (`csegraph`) and CLI package (`csegraph-cli`) depend on this package
and never on each other.
"""

__version__ = "1.4.0"

from csegraph_core.benchmark import BenchmarkService
from csegraph_core.config.profiles import PROFILES, ProfileConfig, get_profile, load_profile
from csegraph_core.core.models import (
    BenchmarkResult,
    BenchmarkStep,
    ContextNode,
    ContextResult,
    GraphEdgeView,
    GraphNodeView,
    GraphResult,
    IndexResult,
    PathEdge,
    PathResult,
    PathStep,
    RefreshResult,
    ReportResult,
    SufficiencyResult,
    VisualExportResult,
    to_dict,
)
from csegraph_core.cse.metrics import SufficiencyMetrics
from csegraph_core.graph.communities import Community, CommunityResult
from csegraph_core.hooks import HooksResult
from csegraph_core.graph.queries import GraphQueryService
from csegraph_core.graph.report import ReportService
from csegraph_core.graph.visual import VisualExportService
from csegraph_core.index.repository import ProjectIndex
from csegraph_core.index.services import IndexService, RefreshService
from csegraph_core.retrieval.constants import VALID_REASONS
from csegraph_core.retrieval.context import ContextService

__all__ = [
    "__version__",
    "BenchmarkResult",
    "BenchmarkService",
    "BenchmarkStep",
    "ContextNode",
    "Community",
    "CommunityResult",
    "ContextResult",
    "ContextService",
    "GraphEdgeView",
    "GraphNodeView",
    "GraphQueryService",
    "GraphResult",
    "HooksResult",
    "IndexResult",
    "IndexService",
    "PathEdge",
    "PathResult",
    "PathStep",
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
