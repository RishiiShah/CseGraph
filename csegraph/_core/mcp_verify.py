from __future__ import annotations

import asyncio
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from csegraph._core.server.tools import CORE_TOOL_NAMES


@dataclass
class McpProtocolVerification:
    state: str
    command: str
    args: list[str]
    expected_tools: list[str] = field(default_factory=list)
    advertised_tools: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    error: str | None = None


def verify_mcp_entry(
    entry: dict[str, Any],
    *,
    expected_tools: Sequence[str] = CORE_TOOL_NAMES,
    timeout_s: float = 10.0,
) -> McpProtocolVerification:
    """Launch the generated CLI MCP server and verify the advertised tool surface.

    This is intentionally an internal installer/doctor check.  Generated MCP
    configuration still launches ``csegraph serve`` directly.
    """

    command = str(entry.get("command") or "")
    args = [str(arg) for arg in entry.get("args") or []]
    if not command:
        return McpProtocolVerification(
            state="launcher_missing",
            command=command,
            args=args,
            expected_tools=list(expected_tools),
            error="MCP entry is missing command",
        )
    if not _launcher_exists(command):
        return McpProtocolVerification(
            state="launcher_missing",
            command=command,
            args=args,
            expected_tools=list(expected_tools),
            error=f"MCP launcher does not exist: {command}",
        )

    try:
        return asyncio.run(
            asyncio.wait_for(
                _verify_mcp_entry_async(entry, expected_tools=list(expected_tools)),
                timeout=timeout_s,
            )
        )
    except Exception as exc:
        return McpProtocolVerification(
            state="protocol_failed",
            command=command,
            args=args,
            expected_tools=list(expected_tools),
            error=str(exc),
        )


async def _verify_mcp_entry_async(
    entry: dict[str, Any],
    *,
    expected_tools: list[str],
) -> McpProtocolVerification:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command = str(entry["command"])
    args = [str(arg) for arg in entry.get("args") or []]
    cwd = entry.get("cwd")
    params = StdioServerParameters(
        command=command,
        args=args,
        cwd=str(cwd) if cwd else None,
        env=dict(os.environ),
    )
    start = time.perf_counter()
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            listed = await session.list_tools()
            advertised = [tool.name for tool in listed.tools]
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

    missing = [tool for tool in expected_tools if tool not in advertised]
    extras = [tool for tool in advertised if tool not in expected_tools]
    if missing or extras:
        bits = []
        if missing:
            bits.append(f"missing tools: {', '.join(missing)}")
        if extras:
            bits.append(f"unexpected tools: {', '.join(extras)}")
        return McpProtocolVerification(
            state="protocol_failed",
            command=command,
            args=args,
            expected_tools=expected_tools,
            advertised_tools=advertised,
            elapsed_ms=elapsed_ms,
            error="; ".join(bits),
        )

    return McpProtocolVerification(
        state="protocol_verified",
        command=command,
        args=args,
        expected_tools=expected_tools,
        advertised_tools=advertised,
        elapsed_ms=elapsed_ms,
    )


def _launcher_exists(command: str) -> bool:
    if "/" in command or "\\" in command:
        return Path(command).is_file()
    return shutil.which(command) is not None
