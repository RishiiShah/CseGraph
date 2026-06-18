"""Private engine namespace for the csegraph distribution.

This package is the source-of-truth context engine for coding agents:
parser, SQLite index, graph traversal, retrieval, and CSE metrics. The public
SDK facade lives at `csegraph`; the CLI lives under `csegraph._cli`. Benchmark,
eval, resolver, embedding, report, and low-level diagnostic services remain
private implementation modules for repo-local maintainer tooling.
"""

__version__ = "1.7.1"

from csegraph._core.config.profiles import PROFILES, ProfileConfig, get_profile, load_profile
from csegraph._core.async_services import (
    AsyncContextService,
    AsyncGraphQueryService,
    AsyncIndexService,
    AsyncRefreshService,
)
from csegraph._core.core.models import (
    ContextNode,
    ContextResult,
    DaemonEntry,
    DaemonResult,
    ExportResult,
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
    RegistryEntry,
    RegistryResult,
    StatusResult,
    SufficiencyResult,
    VisualExportResult,
    to_dict,
)
from csegraph._core.cse.metrics import SufficiencyMetrics
from csegraph._core.graph.exports import EXPORT_FORMATS, ExportService
from csegraph._core.graph.queries import GraphQueryService
from csegraph._core.graph.tree import TreeExportService
from csegraph._core.graph.visual import VisualExportService
from csegraph._core.index.repository import ProjectIndex
from csegraph._core.index.services import IndexService, RefreshService
from csegraph._core.postprocess import POSTPROCESS_LEVELS, PostprocessService
from csegraph._core.retrieval.constants import VALID_REASONS
from csegraph._core.mcp_install import McpInstallService
from csegraph._core.retrieval.context import ContextService
from csegraph._core.retrieval.minimal import MinimalService
from csegraph._core.server.session import SessionState
from csegraph._core.daemon import DaemonService
from csegraph._core.registry import RegistryService
from csegraph._core.status import StatusService

__all__ = [
    "__version__",
    "EXPORT_FORMATS",
    "ExportResult",
    "ExportService",
    "AsyncContextService",
    "AsyncGraphQueryService",
    "AsyncIndexService",
    "AsyncRefreshService",
    "ContextNode",
    "ContextResult",
    "ContextService",
    "DaemonEntry",
    "DaemonResult",
    "DaemonService",
    "GraphEdgeView",
    "GraphNodeView",
    "GraphQueryService",
    "GraphResult",
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
    "POSTPROCESS_LEVELS",
    "PostprocessResult",
    "PostprocessService",
    "PROFILES",
    "ProfileConfig",
    "ProjectIndex",
    "RefreshResult",
    "RefreshService",
    "RegistryEntry",
    "RegistryResult",
    "RegistryService",
    "SessionState",
    "StatusResult",
    "StatusService",
    "SufficiencyMetrics",
    "SufficiencyResult",
    "TreeExportService",
    "VALID_REASONS",
    "VisualExportResult",
    "VisualExportService",
    "get_profile",
    "load_profile",
    "to_dict",
]
