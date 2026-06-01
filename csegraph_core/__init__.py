"""csegraph_core - import namespace for the csegraph-core distribution.

The install/distribution name is `csegraph-core`; Python imports use
`csegraph_core` because hyphens are invalid in module names.

This package is the source-of-truth context engine for coding agents:
parser, SQLite index, graph traversal, retrieval, and CSE metrics. The SDK
package (`csegraph`) and CLI package (`csegraph-cli`) depend on this package
and never on each other. Benchmark, eval, resolver, embedding, report, and
low-level diagnostic services are intentionally not re-exported here; use
their module paths from repo-local maintainer tooling.
"""

__version__ = "1.7.1"

from csegraph_core.config.profiles import PROFILES, ProfileConfig, get_profile, load_profile
from csegraph_core.core.models import (
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
from csegraph_core.cse.metrics import SufficiencyMetrics
from csegraph_core.graph.exports import EXPORT_FORMATS, ExportService
from csegraph_core.graph.queries import GraphQueryService
from csegraph_core.graph.tree import TreeExportService
from csegraph_core.graph.visual import VisualExportService
from csegraph_core.index.repository import ProjectIndex
from csegraph_core.index.services import IndexService, RefreshService
from csegraph_core.postprocess import POSTPROCESS_LEVELS, PostprocessService
from csegraph_core.retrieval.constants import VALID_REASONS
from csegraph_core.mcp_install import McpInstallService
from csegraph_core.retrieval.context import ContextService
from csegraph_core.retrieval.minimal import MinimalService
from csegraph_core.server.session import SessionState
from csegraph_core.daemon import DaemonService
from csegraph_core.registry import RegistryService
from csegraph_core.status import StatusService

__all__ = [
    "__version__",
    "EXPORT_FORMATS",
    "ExportResult",
    "ExportService",
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
