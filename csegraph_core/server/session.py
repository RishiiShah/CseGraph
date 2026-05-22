"""Per-server-process session state for the MCP stdio server.

MCP stdio means one server process per client session; this in-memory state
persists across tool calls within that session and is dropped when the process
exits. The CLI does not share this state — each `csegraph` invocation is its
own process.
"""

from __future__ import annotations

from typing import List


class SessionState:
    def __init__(self) -> None:
        self.tools_called: set[str] = set()
        self.inferred_intent: str | None = None

    def record(self, tool_name: str) -> None:
        if tool_name:
            self.tools_called.add(tool_name)

    def is_called(self, tool_name: str) -> bool:
        return tool_name in self.tools_called

    def snapshot(self) -> List[str]:
        return sorted(self.tools_called)

    def reset(self) -> None:
        self.tools_called.clear()
        self.inferred_intent = None


_SESSION = SessionState()
