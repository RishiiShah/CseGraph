from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def render_json(payload: Dict[str, Any], *, compact: bool) -> str:
    if compact:
        return json.dumps(payload, sort_keys=True)
    return json.dumps(payload, indent=2, sort_keys=True)


def render_context_markdown(payload: Dict[str, Any]) -> str:
    lines: List[str] = [
        "# csegraph context",
        "",
        f"Query: {payload['query']}",
        f"Target: `{payload['target']}`",
        f"Total estimated tokens: {payload['total_estimated_tokens']}",
        f"Sufficient: {payload['sufficiency']['sufficient']}",
        "",
    ]
    for rank, node in enumerate(payload["nodes"], start=1):
        lines.extend(_render_node(rank, node))
    return "\n".join(lines).rstrip() + "\n"


def _render_node(rank: int, node: Dict[str, Any]) -> List[str]:
    path = node["path"]
    line_range = _line_range_text(node.get("line_range"))
    lines = [
        f"## {rank}. `{node['id']}`",
        "",
        f"- Kind: `{node['kind']}`",
        f"- Path: `{path}{line_range}`",
        f"- Reasons: {', '.join(node['reason'])}",
        f"- Estimated tokens: {node['estimated_tokens']}",
    ]
    if node.get("explanation"):
        lines.append(f"- Explanation: {node['explanation']}")
    if node.get("summary"):
        lines.extend(["", node["summary"]])
    if node.get("source_text") is not None:
        lines.extend(["", "```python", node["source_text"].rstrip(), "```"])
    lines.append("")
    return lines


def _line_range_text(line_range: Optional[List[int]]) -> str:
    if not line_range:
        return ""
    return f":{line_range[0]}-{line_range[1]}"
