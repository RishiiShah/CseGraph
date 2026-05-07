from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from csegraph_core.cse.metrics import SufficiencyMetrics


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
