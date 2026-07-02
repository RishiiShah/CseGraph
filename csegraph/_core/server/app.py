from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, GetPromptResult, Prompt, TextContent, Tool

from csegraph._core.core.models import to_dict
from csegraph._core.core.paths import assert_safe_db_path
from csegraph._core.postprocess import attach_postprocess_metadata
from csegraph._core.server.mcp_surface import is_blocking_mcp_tool
from csegraph._core.server.prompts import (
    CORE_MCP_PROMPT_NAMES as _CORE_MCP_PROMPT_NAMES,
)
from csegraph._core.server.prompts import (
    PROMPTS as _PROMPTS,
)
from csegraph._core.server.prompts import (
    handle_prompt as _handle_prompt,
)
from csegraph._core.server.prompts import (
    prompts_for_tools as _prompts_for_tools,
)
from csegraph._core.server.session import _SESSION
from csegraph._core.server.tools import (
    CORE_MCP_TOOL_NAMES as _CORE_MCP_TOOL_NAMES,
)
from csegraph._core.server.tools import (
    CORE_TOOL_NAMES,
)
from csegraph._core.server.tools import (
    MIN_BYTE_CAP as _MIN_BYTE_CAP,
)
from csegraph._core.server.tools import (
    TOOLS as _TOOLS,
)

logger = logging.getLogger("csegraph.mcp")

__all__ = [
    "_CORE_MCP_PROMPT_NAMES",
    "_CORE_MCP_TOOL_NAMES",
    "_PROMPTS",
    "_TOOLS",
    "CORE_TOOL_NAMES",
    "create_server",
    "run_stdio",
]


def _db_path(repo: str, db: str | None = None) -> str:
    repo_path = Path(repo).resolve()
    if db:
        db_path = assert_safe_db_path(db, repo_path, "Database")
        return str(db_path)
    return str(repo_path / ".csegraph" / "index.db")


def _handle_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    bound_repo: str | None = None,
    host_platform: str | None = None,
) -> Any:
    if name not in _CORE_MCP_TOOL_NAMES:
        raise ValueError(f"Unknown tool: {name}")
    if bound_repo and "repo" not in arguments:
        arguments = {**arguments, "repo": bound_repo}
    provided_max = arguments.get("max_bytes")
    if provided_max is not None:
        if isinstance(provided_max, float) and provided_max == int(provided_max):
            provided_max = int(provided_max)
        if not isinstance(provided_max, int):
            raise TypeError(f"max_bytes must be an integer, got {type(provided_max).__name__}")
    if isinstance(provided_max, int) and 0 < provided_max < _MIN_BYTE_CAP:
        raise ValueError(f"max_bytes must be at least {_MIN_BYTE_CAP}")
    result = _dispatch_tool(name, arguments)
    _SESSION.record(name)
    # When the minimal tool runs, cache the detected task intent on the session
    # so downstream calls can route without re-detecting.
    if name == "csegraph_minimal" and isinstance(result, dict):
        intent = result.get("task_intent")
        if intent:
            _SESSION.inferred_intent = intent
    if isinstance(result, dict):
        _apply_trust_metadata(
            result,
            tool=name,
            arguments=arguments,
            bound_repo=bound_repo,
            host_platform=host_platform,
        )
        _apply_session_filter(result)
        _apply_session_token_usage(result)
        effective_max = provided_max if isinstance(provided_max, int) and provided_max > 0 else None
        _apply_byte_cap(result, effective_max)
    _record_tool_call(
        arguments.get("repo") or bound_repo,
        name,
        success=True,
        host_platform=host_platform,
    )
    return result


def _apply_trust_metadata(
    result: dict[str, Any],
    *,
    tool: str,
    arguments: dict[str, Any],
    bound_repo: str | None,
    host_platform: str | None,
) -> None:
    repo = str(arguments.get("repo") or bound_repo or "")
    trust = result.setdefault("trust", {})
    if isinstance(trust, dict):
        trust.setdefault("server", "csegraph")
        trust.setdefault("tool", tool)
        if host_platform:
            trust.setdefault("platform", host_platform)
        if bound_repo:
            trust.setdefault("bound_repo", bound_repo)
        if repo:
            trust.setdefault("repo", repo)
        health = result.get("index_health") or _index_health_payload(repo, arguments.get("db"))
        if health:
            trust.setdefault("index_health", health)
        trust.setdefault(
            "access_contract",
            {
                "surface": "mcp_tools_only",
                "direct_db_reads": "unsupported",
                "platform_scoped": True,
                "platform": host_platform or "unknown",
                "message": (
                    "Use the enabled csegraph MCP server for this host. "
                    "Do not query .csegraph/index.db directly."
                ),
            },
        )
    sufficiency = result.get("sufficiency")
    if isinstance(sufficiency, dict):
        sufficiency.setdefault(
            "verdict",
            "sufficient" if sufficiency.get("sufficient") is True else "not_sufficient",
        )
        metrics = sufficiency.get("metrics")
        if isinstance(metrics, dict):
            sufficiency.setdefault("applicable_metrics", list(metrics))


def _index_health_payload(repo: str, db: str | None = None) -> dict[str, Any] | None:
    if not repo:
        return None
    try:
        from csegraph._core.core.models import to_dict as _to_dict
        from csegraph._core.status import StatusService

        status = StatusService(_db_path(repo, db)).status(verbose=False)
        return _to_dict(status.index_health) if status.index_health is not None else None
    except Exception:
        return None


def _record_tool_call(
    repo: Any,
    tool: str,
    *,
    success: bool,
    host_platform: str | None = None,
) -> None:
    if not repo:
        return
    try:
        repo_path = Path(str(repo)).resolve()
        csegraph_dir = repo_path / ".csegraph"
        csegraph_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "server": "csegraph",
            "tool": tool,
            "success": success,
        }
        if host_platform:
            payload["platform"] = host_platform
        with (csegraph_dir / "mcp_sessions.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    except Exception:
        logger.debug("Failed to record CseGraph MCP session evidence", exc_info=True)


def _apply_session_filter(result: dict[str, Any]) -> None:
    """Drop next-tool suggestions whose tool has already been called this session
    and annotate the response with the current tools_already_called list.
    Mutates `result` in place."""
    called = _SESSION.tools_called
    for key in ("next_tool_suggestions", "next_actions"):
        items = result.get(key)
        if not isinstance(items, list):
            continue
        result[key] = [
            item
            for item in items
            if (isinstance(item, dict) and item.get("action") == "expand_context")
            or not (isinstance(item, dict) and item.get("tool") in called)
        ]
    result["tools_already_called"] = _SESSION.snapshot()


def _apply_session_token_usage(result: dict[str, Any]) -> None:
    response_tokens = _estimate_chars4_tokens(_encoded_size(result))
    usage = result.get("token_usage")
    _SESSION.record_token_usage(
        response_tokens=response_tokens,
        context_usage=usage if isinstance(usage, dict) else None,
    )
    result["session_token_usage"] = _SESSION.token_snapshot()


def _estimate_chars4_tokens(byte_count: int) -> int:
    if byte_count <= 0:
        return 0
    return max(1, math.ceil(byte_count / 4))


def _encoded_size(result: dict[str, Any]) -> int:
    return len(json.dumps(result, default=str).encode("utf-8"))


def _apply_byte_cap(result: dict[str, Any], max_bytes: int | None) -> None:
    """Enforce a hard ceiling on the serialized response size.

    Drop order (each step re-measures; stops when under budget):
      1. `source_text` on every symbol/node
      2. `explanation` on every symbol/node
      3. Drop `import_preludes`
      4. Drop `relationships[].occurrences[].snippet`
      5. Trim `relationships`
      6. Trim `symbols`/`nodes` from the tail (ordering = priority)
      7. Trim graph `edges` from the tail

    Annotates the response with `response_bytes`, `byte_cap`, `byte_cap_applied`,
    and `truncated_fields` so the agent knows what was dropped. Mutates `result`
    in place.

    Annotation fields are added BEFORE measurement so every size check reflects
    the final response shape. `response_bytes` is the placeholder initially and
    is overwritten with the true final size at the end.
    """
    truncated: list[str] = []
    result["truncated_fields"] = truncated
    result["byte_cap_applied"] = False
    if isinstance(max_bytes, int) and max_bytes > 0:
        result["byte_cap"] = max_bytes
    # Placeholder; we set the final value at the end. Use a value with similar
    # digit count to the cap so the size measurement stays stable.
    result["response_bytes"] = max_bytes if (isinstance(max_bytes, int) and max_bytes > 0) else 0

    if not isinstance(max_bytes, int) or max_bytes <= 0:
        _finalize_response_bytes(result)
        return

    if _encoded_size(result) <= max_bytes:
        _finalize_response_bytes(result)
        return

    nodes_key = "symbols" if isinstance(result.get("symbols"), list) else "nodes"
    nodes = result.get(nodes_key)

    # Step 1: drop source_text from every node.
    if isinstance(nodes, list):
        dropped = False
        for node in nodes:
            if isinstance(node, dict) and node.get("source_text") is not None:
                node.pop("source_text", None)
                dropped = True
        if dropped:
            _mark_truncated(truncated, "source_text")
            if _encoded_size(result) <= max_bytes:
                result["byte_cap_applied"] = True
                _finalize_response_bytes(result)
                return

    # Step 2: drop explanation from every node.
    if isinstance(nodes, list):
        dropped = False
        for node in nodes:
            if isinstance(node, dict) and node.get("explanation") is not None:
                node.pop("explanation", None)
                dropped = True
        if dropped:
            _mark_truncated(truncated, "explanation")
            if _encoded_size(result) <= max_bytes:
                result["byte_cap_applied"] = True
                _finalize_response_bytes(result)
                return

    # Step 3: drop import preludes.
    if isinstance(result.get("import_preludes"), list) and result["import_preludes"]:
        result["import_preludes"] = []
        _mark_truncated(truncated, "import_preludes")
        if _encoded_size(result) <= max_bytes:
            result["byte_cap_applied"] = True
            _finalize_response_bytes(result)
            return

    # Step 4: drop source snippets from relationship metadata/reference payloads.
    relationships = result.get("relationships")
    if isinstance(relationships, list):
        dropped = False
        for relationship in relationships:
            if not isinstance(relationship, dict):
                continue
            metadata = relationship.get("metadata")
            if isinstance(metadata, dict) and metadata.get("source") is not None:
                metadata.pop("source", None)
                dropped = True
            occurrences = relationship.get("occurrences")
            if isinstance(occurrences, list):
                for occurrence in occurrences:
                    if isinstance(occurrence, dict) and occurrence.get("snippet") is not None:
                        occurrence.pop("snippet", None)
                        dropped = True
        if dropped:
            _mark_truncated(truncated, "relationship_snippets")
            if _encoded_size(result) <= max_bytes:
                result["byte_cap_applied"] = True
                _finalize_response_bytes(result)
                return

    # Step 5: trim relationships.
    if isinstance(relationships, list) and relationships:
        while relationships and _encoded_size(result) > max_bytes:
            _pop_omitted(result, "relationships")
        if "relationships" in result.get("omitted_counts", {}):
            _mark_truncated(truncated, "relationships")
            if _encoded_size(result) <= max_bytes:
                result["byte_cap_applied"] = True
                _finalize_response_bytes(result)
                return

    # Step 6: trim symbols/nodes list (lowest-priority assumed at tail).
    if isinstance(nodes, list) and nodes:
        while len(nodes) > 1 and _encoded_size(result) > max_bytes:
            _pop_omitted(result, nodes_key)
        if nodes_key in result.get("omitted_counts", {}):
            _mark_truncated(truncated, nodes_key)
            if _encoded_size(result) <= max_bytes:
                result["byte_cap_applied"] = True
                _finalize_response_bytes(result)
                return

    # Step 7: trim graph edges list.
    edges = result.get("edges")
    if isinstance(edges, list) and edges:
        while edges and _encoded_size(result) > max_bytes:
            _pop_omitted(result, "edges")
        if "edges" in result.get("omitted_counts", {}):
            _mark_truncated(truncated, "edges")
            if _encoded_size(result) <= max_bytes:
                result["byte_cap_applied"] = True
                _finalize_response_bytes(result)
                return

    # Step 5: trim known non-node result shapes in deterministic priority order.
    for key in ("low_risk", "medium_risk", "high_risk", "flows"):
        if _encoded_size(result) <= max_bytes:
            break
        _trim_list_field(result, key, max_bytes, truncated)

    # Step 6 (generic): trim any remaining list-valued payload keys.
    if _encoded_size(result) > max_bytes:
        _generic_list_trim(result, max_bytes, truncated)

    if _encoded_size(result) > max_bytes:
        _final_compact_to_cap(result, max_bytes, truncated)

    result["byte_cap_applied"] = bool(truncated)
    _finalize_response_bytes(result)
    if result["response_bytes"] > max_bytes:
        _replace_with_minimal_cap_notice(result, max_bytes, truncated)
        _finalize_response_bytes(result)


def _finalize_response_bytes(result: dict[str, Any]) -> None:
    """Converge `response_bytes` to the actual encoded size.

    Setting `response_bytes` may shift the encoded length by a few bytes when
    the value's digit count differs from the placeholder. A short fixed-point
    loop converges in 1-2 iterations on every realistic payload.
    """
    for _ in range(4):
        new_size = _encoded_size(result)
        if result.get("response_bytes") == new_size:
            return
        result["response_bytes"] = new_size


_TRIM_SKIP_KEYS = frozenset(
    {
        "session_token_usage",
        "truncated_fields",
        "tools_already_called",
        "warnings",
        "omitted_counts",
    }
)


def _mark_truncated(truncated: list[str], key: str) -> None:
    if key not in truncated:
        truncated.append(key)


def _pop_omitted(result: dict[str, Any], key: str) -> None:
    items = result.get(key)
    if not isinstance(items, list) or not items:
        return
    items.pop()
    counts = result.setdefault("omitted_counts", {})
    counts[key] = counts.get(key, 0) + 1


def _trim_list_field(
    result: dict[str, Any],
    key: str,
    max_bytes: int,
    truncated: list[str],
    *,
    min_items: int = 0,
) -> None:
    items = result.get(key)
    if not isinstance(items, list):
        return
    before = len(items)
    while len(items) > min_items and _encoded_size(result) > max_bytes:
        _pop_omitted(result, key)
    if len(items) != before:
        _mark_truncated(truncated, key)


def _generic_list_trim(result: dict[str, Any], max_bytes: int, truncated: list[str]) -> None:
    """Trim list-valued payload keys deterministically until under budget."""
    while _encoded_size(result) > max_bytes:
        candidates = [
            k for k, v in result.items() if isinstance(v, list) and v and k not in _TRIM_SKIP_KEYS
        ]
        if not candidates:
            break
        largest_key = max(candidates, key=lambda k: len(result[k]))
        _pop_omitted(result, largest_key)
        _mark_truncated(truncated, largest_key)


def _final_compact_to_cap(result: dict[str, Any], max_bytes: int, truncated: list[str]) -> None:
    """Last-resort compaction that keeps cap metadata and drops payload bulk."""
    for key, value in list(result.items()):
        if _encoded_size(result) <= max_bytes:
            return
        if key in _TRIM_SKIP_KEYS:
            continue
        if isinstance(value, list) and value:
            counts = result.setdefault("omitted_counts", {})
            counts[key] = counts.get(key, 0) + len(value)
            result[key] = []
            _mark_truncated(truncated, key)

    for preferred in ("summary", "message", "error"):
        if _encoded_size(result) <= max_bytes:
            return
        _truncate_string_field(result, preferred, max_bytes, truncated)

    for key, value in list(result.items()):
        if _encoded_size(result) <= max_bytes:
            return
        if isinstance(value, str) and key not in {"command", "byte_cap"}:
            _truncate_string_field(result, key, max_bytes, truncated)

    for key in ("warnings", "tools_already_called"):
        if _encoded_size(result) <= max_bytes:
            return
        value = result.get(key)
        if isinstance(value, list) and value:
            counts = result.setdefault("omitted_counts", {})
            counts[key] = counts.get(key, 0) + len(value)
            result[key] = []
            _mark_truncated(truncated, key)


def _truncate_string_field(
    result: dict[str, Any], key: str, max_bytes: int, truncated: list[str]
) -> None:
    value = result.get(key)
    if not isinstance(value, str) or not value:
        return
    while value and _encoded_size(result) > max_bytes:
        excess = _encoded_size(result) - max_bytes
        keep = max(0, len(value) - excess - 16)
        value = value[:keep]
        result[key] = value + ("..." if keep > 0 else "")
    _mark_truncated(truncated, key)


def _replace_with_minimal_cap_notice(
    result: dict[str, Any], max_bytes: int, truncated: list[str]
) -> None:
    counts = result.get("omitted_counts", {})
    omitted_total = sum(v for v in counts.values() if isinstance(v, int))
    command = result.get("command")
    truncated_snapshot = list(truncated) or ["response"]
    result.clear()
    if command:
        result["command"] = command
    result["byte_cap"] = max_bytes
    result["byte_cap_applied"] = True
    result["truncated_fields"] = truncated_snapshot
    if counts:
        result["omitted_counts"] = counts
    result["summary"] = "Response compacted to satisfy max_bytes."
    result["response_bytes"] = max_bytes
    if _encoded_size(result) <= max_bytes:
        return

    result["truncated_fields"] = ["response"]
    if omitted_total:
        result["omitted_counts"] = {"response": omitted_total}
    result["summary"] = "Response compacted to satisfy max_bytes."
    if _encoded_size(result) <= max_bytes:
        return

    result.pop("summary", None)
    if _encoded_size(result) <= max_bytes:
        return

    result.pop("command", None)


def _dispatch_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == "csegraph_index":
        from csegraph._core.graph.queries import clear_hub_cache as clear_graph_hub_cache
        from csegraph._core.index.services import IndexService
        from csegraph._core.postprocess import PostprocessService
        from csegraph._core.retrieval.cache import CACHE
        from csegraph._core.retrieval.minimal import clear_hub_cache as clear_minimal_hub_cache

        repo = arguments["repo"]
        profile = arguments.get("profile", "auto")
        db = _db_path(repo, arguments.get("db"))
        index_result = IndexService(db).index(repo, profile=profile)
        pp_level = arguments.get("postprocess_level", "full")
        pp_result = None
        skipped_reason = None
        if pp_level != "none":
            pp_result = PostprocessService(db).postprocess(level=pp_level)
        else:
            skipped_reason = "disabled"
        attach_postprocess_metadata(index_result, db, pp_level, pp_result, skipped_reason)
        clear_graph_hub_cache()
        clear_minimal_hub_cache()
        CACHE.clear(db)
        return to_dict(index_result)

    if name == "csegraph_refresh":
        from csegraph._core.graph.queries import clear_hub_cache as clear_graph_hub_cache
        from csegraph._core.index.services import RefreshService
        from csegraph._core.postprocess import PostprocessService
        from csegraph._core.retrieval.cache import CACHE
        from csegraph._core.retrieval.minimal import clear_hub_cache as clear_minimal_hub_cache

        repo = arguments["repo"]
        profile = arguments.get("profile", "auto")
        db = _db_path(repo, arguments.get("db"))
        refresh_result = RefreshService(db).refresh(profile=profile)
        pp_level = arguments.get("postprocess_level", "full")
        pp_result = None
        skipped_reason = None
        if pp_level != "none" and refresh_result.files_indexed > 0:
            pp_result = PostprocessService(db).postprocess(level=pp_level)
        elif pp_level == "none":
            skipped_reason = "disabled"
        else:
            skipped_reason = "unchanged"
        attach_postprocess_metadata(refresh_result, db, pp_level, pp_result, skipped_reason)
        clear_graph_hub_cache()
        clear_minimal_hub_cache()
        CACHE.clear(db)
        return to_dict(refresh_result)

    if name == "csegraph_minimal":
        from csegraph._core.retrieval.minimal import MinimalService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        return to_dict(
            MinimalService(db).first(
                task=arguments.get("task"),
                inferred_intent=_SESSION.inferred_intent,
            )
        )

    if name == "csegraph_context":
        from csegraph._core.retrieval.context import ContextService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        return to_dict(
            ContextService(db).build_context(
                task=arguments["task"],
                target=arguments.get("target"),
                profile=arguments.get("profile", "auto"),
                include_source=arguments.get("include_source", "auto"),
                max_tokens=arguments.get("max_tokens"),
                explain=arguments.get("explain", False),
                detail_level=arguments.get("detail_level", "auto"),
                task_kind=arguments.get("task_kind", "auto"),
            )
        )

    if name == "csegraph_graph":
        from csegraph._core.graph.queries import GraphQueryService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        depth = arguments.get("depth", 1)
        detail_level = arguments.get("detail_level", "minimal")
        relations = arguments.get("relations")
        confidence_tiers = arguments.get("confidence_tiers")
        return to_dict(
            GraphQueryService(db).neighborhood(
                arguments["node"],
                depth=depth,
                detail_level=detail_level,
                relations=relations,
                confidence_tiers=confidence_tiers,
            )
        )

    if name == "csegraph_path":
        from csegraph._core.graph.queries import GraphQueryService

        repo = arguments["repo"]
        db = _db_path(repo, arguments.get("db"))
        detail_level = arguments.get("detail_level", "minimal")
        relations = arguments.get("relations")
        confidence_tiers = arguments.get("confidence_tiers")
        return to_dict(
            GraphQueryService(db).shortest_path(
                arguments["source"],
                arguments["target"],
                detail_level=detail_level,
                relations=relations,
                confidence_tiers=confidence_tiers,
            )
        )

    raise ValueError(f"Unknown tool: {name}")


_SERVER_INSTRUCTIONS = (
    "CseGraph is a local-first code context engine. Use only the advertised "
    "csegraph_* MCP tools. Start with csegraph_minimal, follow one suggested "
    "next tool, and avoid broad repo reads when CseGraph can supply the slice. "
    "Do not query .csegraph/index.db directly; it is a private implementation "
    "detail behind the MCP tools."
)


def create_server(
    *,
    allowed_tools: list[str] | None = None,
    bound_repo: str | None = None,
    host_platform: str | None = None,
) -> Server:
    if allowed_tools is None:
        allowed_tools = CORE_TOOL_NAMES
    unknown = set(allowed_tools) - {t.name for t in _TOOLS}
    if unknown:
        raise ValueError(f"Unknown tool names in --tools filter: {sorted(unknown)}")
    allowed_tool_names = set(allowed_tools)
    tools = [t for t in _TOOLS if t.name in allowed_tools]
    prompts = _prompts_for_tools(allowed_tool_names)
    allowed_prompt_names = {prompt.name for prompt in prompts}

    instructions = _SERVER_INSTRUCTIONS
    if bound_repo:
        instructions += f" This server is bound to repo: {bound_repo}."
    if host_platform:
        instructions += f" This server was launched for platform: {host_platform}."
    server = Server("csegraph", instructions=instructions)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return tools

    @server.list_prompts()
    async def list_prompts() -> list[Prompt]:
        return prompts

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
        if name not in allowed_prompt_names:
            raise ValueError(f"Prompt '{name}' is not enabled for this server")
        return _handle_prompt(name, dict(arguments or {}))

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent] | CallToolResult:
        try:
            if name not in allowed_tool_names:
                raise ValueError(f"Tool '{name}' is not enabled for this server")
            if is_blocking_mcp_tool(name):
                result = await asyncio.to_thread(
                    _handle_tool,
                    name,
                    arguments,
                    bound_repo=bound_repo,
                    host_platform=host_platform,
                )
            else:
                result = _handle_tool(
                    name,
                    arguments,
                    bound_repo=bound_repo,
                    host_platform=host_platform,
                )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            _record_tool_call(
                arguments.get("repo") or bound_repo,
                name,
                success=False,
                host_platform=host_platform,
            )
            error_payload = {"error": str(exc), "tool": name}
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(error_payload, indent=2),
                    )
                ],
                isError=True,
            )

    return server


async def run_stdio(
    *,
    allowed_tools: list[str] | None = None,
    bound_repo: str | None = None,
    host_platform: str | None = None,
) -> None:
    server = create_server(
        allowed_tools=allowed_tools,
        bound_repo=bound_repo,
        host_platform=host_platform,
    )
    if allowed_tools:
        logger.info("csegraph MCP server running on stdio; exposing %s tools", len(allowed_tools))
    else:
        logger.info("csegraph MCP server running on stdio; waiting for client connection")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
