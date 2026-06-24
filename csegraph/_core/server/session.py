"""Per-server-process session state for the MCP stdio server.

MCP stdio means one server process per client session; this in-memory state
persists across tool calls within that session and is dropped when the process
exits. The CLI does not share this state — each `csegraph` invocation is its
own process.
"""

from __future__ import annotations

from typing import Any, List


def _int_value(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


class SessionState:
    def __init__(self) -> None:
        self.tools_called: set[str] = set()
        self.inferred_intent: str | None = None
        self.tool_call_count = 0
        self.response_tokens = 0
        self.context_used_tokens = 0
        self.context_baseline_tokens = 0
        self.context_saved_tokens = 0

    def record(self, tool_name: str) -> None:
        if tool_name:
            self.tools_called.add(tool_name)
            self.tool_call_count += 1

    def record_token_usage(
        self,
        *,
        response_tokens: int,
        context_usage: dict[str, Any] | None = None,
    ) -> None:
        self.response_tokens += max(0, response_tokens)
        if isinstance(context_usage, dict):
            self.context_used_tokens += _int_value(context_usage.get("used_tokens"))
            self.context_baseline_tokens += _int_value(context_usage.get("baseline_tokens"))
            self.context_saved_tokens += _int_value(context_usage.get("saved_tokens"))

    def is_called(self, tool_name: str) -> bool:
        return tool_name in self.tools_called

    def snapshot(self) -> List[str]:
        return sorted(self.tools_called)

    def token_snapshot(self) -> dict[str, Any]:
        ratio = None
        if self.context_baseline_tokens and self.context_used_tokens:
            ratio = round(self.context_baseline_tokens / self.context_used_tokens, 2)
        return {
            "estimator": "chars/4 proxy",
            "scope": "mcp_session",
            "calls": self.tool_call_count,
            "response_tokens": self.response_tokens,
            "context_used_tokens": self.context_used_tokens,
            "baseline_tokens": self.context_baseline_tokens,
            "saved_tokens": self.context_saved_tokens,
            "reduction_ratio": ratio,
        }

    def reset(self) -> None:
        self.tools_called.clear()
        self.inferred_intent = None
        self.tool_call_count = 0
        self.response_tokens = 0
        self.context_used_tokens = 0
        self.context_baseline_tokens = 0
        self.context_saved_tokens = 0


_SESSION = SessionState()
