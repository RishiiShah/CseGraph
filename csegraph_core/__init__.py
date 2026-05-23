"""csegraph_core - import namespace for the csegraph-core distribution.

The install/distribution name is `csegraph-core`; Python imports use
`csegraph_core` because hyphens are invalid in module names.

This package is the source-of-truth context engine for coding agents:
parser, SQLite index, graph traversal, retrieval, and CSE metrics. The SDK
package (`csegraph`) and CLI package (`csegraph-cli`) depend on this package
and never on each other.
"""

__version__ = "1.6.0"

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
    KeyEntity,
    McpInstallResult,
    McpInstallTarget,
    MinimalResult,
    NextToolSuggestion,
    PathEdge,
    PathResult,
    PathStep,
    PostprocessResult,
    RefreshResult,
    ReportResult,
    StatusResult,
    SufficiencyResult,
    VisualExportResult,
    to_dict,
)
from csegraph_core.cse.metrics import SufficiencyMetrics
from csegraph_core.graph.change_detection import (
    ChangeDetectionResult,
    ChangeDetectionService,
    ChangedSymbol,
    DiffRegion,
)
from csegraph_core.graph.communities import Community, CommunityResult
from csegraph_core.graph.review_eval import (
    ReviewEvalResult,
    ReviewEvalService,
    RiskLevelMetrics,
)
from csegraph_core.graph.review_questions import (
    ReviewQuestion,
    ReviewQuestionsResult,
    ReviewQuestionsService,
)
from csegraph_core.graph.test_gaps import (
    CommunityCoverage,
    TestGapResult,
    TestGapService,
    UntestedSymbol,
)
from csegraph_core.hooks import HooksResult
from csegraph_core.graph.queries import GraphQueryService
from csegraph_core.graph.report import ReportService
from csegraph_core.graph.tree import TreeExportService
from csegraph_core.graph.visual import VisualExportService
from csegraph_core.index.repository import ProjectIndex
from csegraph_core.index.services import IndexService, RefreshService
from csegraph_core.postprocess import PostprocessService
from csegraph_core.retrieval.constants import VALID_REASONS
from csegraph_core.mcp_install import McpInstallService
from csegraph_core.retrieval.context import ContextService
from csegraph_core.retrieval.minimal import MinimalService
from csegraph_core.server.session import SessionState
from csegraph_core.status import StatusService

__all__ = [
    "__version__",
    "BenchmarkResult",
    "BenchmarkService",
    "BenchmarkStep",
    "ChangeDetectionResult",
    "ChangeDetectionService",
    "ChangedSymbol",
    "CommunityCoverage",
    "Community",
    "CommunityResult",
    "ContextNode",
    "ContextResult",
    "ContextService",
    "DiffRegion",
    "GraphEdgeView",
    "GraphNodeView",
    "GraphQueryService",
    "GraphResult",
    "HooksResult",
    "IndexResult",
    "IndexService",
    "KeyEntity",
    "McpInstallResult",
    "McpInstallService",
    "McpInstallTarget",
    "MinimalResult",
    "MinimalService",
    "NextToolSuggestion",
    "PathEdge",
    "PathResult",
    "PathStep",
    "PostprocessResult",
    "PostprocessService",
    "PROFILES",
    "ProfileConfig",
    "ProjectIndex",
    "RefreshResult",
    "RefreshService",
    "ReportResult",
    "ReportService",
    "ReviewEvalResult",
    "ReviewEvalService",
    "ReviewQuestion",
    "ReviewQuestionsResult",
    "ReviewQuestionsService",
    "RiskLevelMetrics",
    "SessionState",
    "StatusResult",
    "StatusService",
    "SufficiencyMetrics",
    "SufficiencyResult",
    "TestGapResult",
    "TestGapService",
    "TreeExportService",
    "UntestedSymbol",
    "VALID_REASONS",
    "VisualExportResult",
    "VisualExportService",
    "get_profile",
    "load_profile",
    "to_dict",
]
