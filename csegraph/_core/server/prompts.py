"""MCP prompt catalog and rendering helpers."""

from __future__ import annotations

import json
from typing import Any

from mcp.types import GetPromptResult, Prompt, PromptArgument, PromptMessage, TextContent

PROMPTS: list[Prompt] = [
    Prompt(
        name="csegraph-index",
        title="Index Repository",
        description="Build or rebuild the csegraph index for a repository.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(
                name="profile", description="auto, small, medium, or large.", required=False
            ),
        ],
    ),
    Prompt(
        name="csegraph-refresh",
        title="Refresh Repository",
        description="Refresh changed and deleted files in an existing csegraph index.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(
                name="profile", description="auto, small, medium, or large.", required=False
            ),
        ],
    ),
    Prompt(
        name="csegraph-minimal",
        title="Routing Card (Call First)",
        description="Run csegraph_minimal first to get a compact summary and next-tool suggestions.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(
                name="task",
                description="Optional task description for keyword routing.",
                required=False,
            ),
        ],
    ),
    Prompt(
        name="csegraph-context",
        title="Retrieve Context",
        description="Retrieve compact graph-backed context for a task and optional target.",
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(name="task", description="Natural-language coding task.", required=True),
            PromptArgument(
                name="target", description="Optional symbol, node ID, or file path.", required=False
            ),
        ],
    ),
    Prompt(
        name="csegraph-debug-issue",
        title="Debug Issue",
        description=(
            "Guided debugging using the routing card, task context, and one graph neighborhood. "
            "Replaces broad grep and full-file reads."
        ),
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(
                name="description",
                description="Bug symptom, error message, or failing behavior.",
                required=True,
            ),
            PromptArgument(
                name="target",
                description="Optional failing symbol, file path, or node ID.",
                required=False,
            ),
        ],
    ),
    Prompt(
        name="csegraph-review-changes",
        title="Review Changes",
        description=(
            "Pre-commit review workflow using graph-backed context. "
            "Use terminal `csegraph analyze` for git-scoped risk and diagnostics (CLI-only)."
        ),
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(
                name="task",
                description="What changed or what to review (e.g. auth module edits).",
                required=True,
            ),
            PromptArgument(
                name="base",
                description="Optional git base ref for human CLI diff commands (default HEAD~1).",
                required=False,
            ),
        ],
    ),
    Prompt(
        name="csegraph-pre-merge-check",
        title="Pre-Merge Check",
        description=(
            "PR readiness using minimal routing, task context, and optional dependency inspection. "
            "Stays within the six core MCP tools."
        ),
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(
                name="task",
                description="Merge or PR summary (branch purpose, risky areas).",
                required=True,
            ),
        ],
    ),
    Prompt(
        name="csegraph-explore-architecture",
        title="Explore Architecture",
        description=(
            "Map subsystem structure with a routing card and hub-aware graph neighborhood. "
            "For human HTML exports use CLI `csegraph export --format html`."
        ),
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(
                name="focus",
                description="Optional subsystem, symbol, or area to explore.",
                required=False,
            ),
        ],
    ),
    Prompt(
        name="csegraph-onboard-developer",
        title="Onboard Developer",
        description=(
            "Orient a new contributor: routing card, overview context, and one structural graph call."
        ),
        arguments=[
            PromptArgument(name="repo", description="Absolute repository path.", required=True),
            PromptArgument(
                name="focus",
                description="Optional area of interest (e.g. retrieval, MCP server).",
                required=False,
            ),
        ],
    ),
]

CORE_MCP_PROMPT_NAMES = (
    "csegraph-index",
    "csegraph-refresh",
    "csegraph-minimal",
    "csegraph-context",
    "csegraph-debug-issue",
    "csegraph-review-changes",
    "csegraph-pre-merge-check",
    "csegraph-explore-architecture",
    "csegraph-onboard-developer",
)
PROMPTS = [prompt for prompt in PROMPTS if prompt.name in CORE_MCP_PROMPT_NAMES]

PROMPT_TOOL_DEPENDENCIES: dict[str, set[str]] = {
    "csegraph-index": {"csegraph_index"},
    "csegraph-refresh": {"csegraph_refresh"},
    "csegraph-minimal": {"csegraph_minimal"},
    "csegraph-context": {"csegraph_minimal", "csegraph_context", "csegraph_graph"},
    "csegraph-debug-issue": {"csegraph_minimal", "csegraph_context", "csegraph_graph"},
    "csegraph-review-changes": {"csegraph_refresh", "csegraph_minimal", "csegraph_context"},
    "csegraph-pre-merge-check": {"csegraph_minimal", "csegraph_context", "csegraph_graph"},
    "csegraph-explore-architecture": {"csegraph_minimal", "csegraph_graph"},
    "csegraph-onboard-developer": {"csegraph_minimal", "csegraph_context", "csegraph_graph"},
}


# Prefixed to every prompt to enforce token-efficiency and escalation rules.
TOKEN_EFFICIENCY_PREAMBLE = (
    "Token-efficiency: Prefer fewer tool calls and smaller payloads. "
    "Never make more than 3 tool calls in a single agent turn. "
    "If a minimal routing card is sufficient, prefer it to additional heavy calls."
)


def handle_prompt(name: str, arguments: dict[str, Any] | None = None) -> GetPromptResult:
    if name not in CORE_MCP_PROMPT_NAMES:
        raise ValueError(f"Unknown prompt: {name}")
    args = arguments or {}
    if name == "csegraph-index":
        text = prompt_text(
            "Build or rebuild the csegraph repository index.",
            [
                "If `repo` is missing, ask the user for the absolute repository path.",
                "Call `csegraph_index` with the repo path and profile (default auto).",
                "Report: files indexed, symbols, edges, cache hits/misses, parse errors.",
                "If parse errors > 0, list them so the user can fix syntax before relying on the graph.",
            ],
            args,
        )
    elif name == "csegraph-refresh":
        text = prompt_text(
            "Incrementally refresh the csegraph index for changed and deleted files.",
            [
                "If `repo` is missing, ask the user for the absolute repository path.",
                "Call `csegraph_refresh` with the repo path and profile (default auto).",
                "Report: changed files re-indexed, deleted files removed, unchanged files kept.",
                "If parse errors appear on changed files, flag them.",
            ],
            args,
        )
    elif name == "csegraph-minimal":
        text = prompt_text(
            "Get an optional compact repository-health and orientation card.",
            [
                "If `repo` is missing, ask the user for the absolute repository path.",
                "Call `csegraph_minimal` only when index health or high-level entry points are needed.",
                "For task-specific code, call `csegraph_context` directly instead.",
            ],
            args,
        )
    elif name == "csegraph-context":
        text = prompt_text(
            "Retrieve an exact-budget task-specific code slice in one call.",
            [
                "Call `csegraph_context` directly with the repo, task, and target when known.",
                "Use the returned `slices` before opening files or searching the repository.",
                "If status is `ambiguous`, retry once with a returned candidate ID.",
                "If `next` recommends graph or path, make that one focused structural call.",
                "Do not call `csegraph_minimal` first unless the user explicitly asks for orientation or index health.",
                "Stop after one call for ordinary tasks and two calls for structural escalation.",
            ],
            args,
        )
    elif name == "csegraph-debug-issue":
        text = prompt_text(
            "Debug a reported issue using graph-backed context instead of repo-wide search.",
            [
                "If `repo` is missing, ask for the absolute repository path.",
                "Call `csegraph_context` with task=description and the failing target when known.",
                "Inspect the returned target, callers, tests, and dependency slices before opening files.",
                "Only if `next` requests it, call `csegraph_graph` on the failing symbol at depth 1.",
                "Do not use broad grep or read whole files unless context is insufficient after these steps.",
                "Stop after at most 2 csegraph MCP tool calls.",
            ],
            args,
        )
    elif name == "csegraph-review-changes":
        base = args.get("base", "HEAD~1")
        text = prompt_text(
            "Review recent changes using compact graph context (context-engine workflow).",
            [
                "If `repo` is missing, ask for the absolute repository path.",
                "Optional (human terminal, not MCP): run `csegraph analyze --base-ref "
                + repr(base)
                + "` for risk-ranked diagnostics.",
                "Call `csegraph_context` with the review task and highest-risk target; it refreshes changed files automatically.",
                "Use returned target, caller, dependency, and test slices before reading changed files.",
                "Only make a focused graph call if the response recommends structural escalation.",
                "Stop after at most 2 csegraph MCP tool calls.",
            ],
            args,
        )
    elif name == "csegraph-pre-merge-check":
        text = prompt_text(
            "Assess merge/PR readiness with minimal context cost.",
            [
                "If `repo` is missing, ask for the absolute repository path.",
                "Call `csegraph_context` on the highest-risk area named in the merge/PR summary.",
                "Use the compact caller, dependency, and test slices to assess local risk.",
                "Only if `next` recommends it, call `csegraph_graph` with depth=1 on one critical symbol.",
                "Report response status, freshness, budget use, and whether structural context remains.",
                "Use only the six core csegraph MCP tools; run `csegraph analyze` via CLI if the user asks for diagnostics.",
                "Stop after at most 2 csegraph MCP tool calls.",
            ],
            args,
        )
    elif name == "csegraph-explore-architecture":
        focus = args.get("focus") or "a high-degree key entity from the routing card"
        text = prompt_text(
            "Explore repository architecture with a routing card and one graph neighborhood.",
            [
                "If `repo` is missing, ask for the absolute repository path.",
                "Step 1: Call `csegraph_minimal` (task may mention the focus area).",
                "Step 2: Call `csegraph_graph` on "
                + repr(focus)
                + ' with depth=2 and detail_level=minimal; use relations=["calls","imports"] if exploring dependencies.',
                "Summarize modules, coupling hints from confidence_breakdown/hubs_skipped, and suggested next targets.",
                "Stop after at most 3 csegraph MCP tool calls (second call may be another graph if focus was wrong).",
            ],
            args,
        )
    elif name == "csegraph-onboard-developer":
        text = prompt_text(
            "Onboard a developer to the codebase using graph-backed orientation.",
            [
                "If `repo` is missing, ask for the absolute repository path.",
                "Step 1: Call `csegraph_minimal` to surface key entities and languages.",
                "Step 2: Call `csegraph_context` with task describing onboarding goals and optional focus area.",
                "Step 3: Call `csegraph_graph` on one key entity at depth=1 for structural orientation.",
                "Produce a short guide: entry symbols, main languages, and where to pull task context next.",
                "Stop after at most 3 csegraph MCP tool calls.",
            ],
            args,
        )
    else:
        raise ValueError(f"Unknown prompt: {name}")

    return GetPromptResult(
        description=f"CseGraph workflow prompt: {name}",
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(type="text", text=text),
            )
        ],
    )


def prompt_text(goal: str, steps: list[str], arguments: dict[str, Any]) -> str:
    args_text = json.dumps(arguments, sort_keys=True)
    lines = [
        TOKEN_EFFICIENCY_PREAMBLE,
        "",
        goal,
        "",
        f"Arguments: {args_text}",
        "",
        "Workflow:",
    ]
    lines.extend(f"- {step}" for step in steps)
    return "\n".join(lines)


def prompts_for_tools(allowed_tool_names: set[str]) -> list[Prompt]:
    return [
        prompt
        for prompt in PROMPTS
        if PROMPT_TOOL_DEPENDENCIES.get(prompt.name, set()).issubset(allowed_tool_names)
    ]
