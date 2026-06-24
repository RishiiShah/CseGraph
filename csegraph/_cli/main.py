"""csegraph._cli.main - CLI entrypoint for csegraph.

Provides the `csegraph` shell command registered by the root pyproject.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from csegraph._cli.errors import CsegraphCLIError, error_payload
from csegraph._cli.renderer import (
    render_analyze_summary,
    render_architecture_summary,
    render_benchmark_summary,
    render_communities_summary,
    render_context_markdown,
    render_daemon_summary,
    render_detect_changes_summary,
    render_embeddings_summary,
    render_export_summary,
    render_flows_summary,
    render_index_summary,
    render_install_summary,
    render_json,
    render_path_summary,
    render_postprocess_summary,
    render_refresh_summary,
    render_registry_summary,
    render_report_markdown,
    render_resolvers_summary,
    render_review_eval_summary,
    render_review_questions_summary,
    render_status_summary,
    render_test_gaps_summary,
    render_vulnerabilities_summary,
)
from csegraph._core.config.profiles import PROFILE_CHOICES
from csegraph._core.core.models import to_dict
from csegraph._core.core.paths import assert_safe_db_path
from csegraph._core.postprocess import attach_postprocess_metadata


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return _run(args)


def dev_main(argv: list[str] | None = None) -> int:
    parser = _build_dev_parser()
    args = parser.parse_args(argv)
    return _run(args)


def _run(args: argparse.Namespace) -> int:
    _configure_logging(args)
    command = _effective_command(args)

    try:
        _validate_cli_options(args)
        result = _dispatch(args)
    except Exception as exc:
        print(json.dumps(error_payload(exc), indent=2, sort_keys=True), file=sys.stderr)
        return 1

    if result is None:
        return 0
    payload = to_dict(result)
    if command == "context" and args.output_format == "markdown":
        print(render_context_markdown(payload), end="")
    elif command == "analyze" and not args.json:
        print(render_analyze_summary(payload), end="")
    elif command == "report" and not args.json:
        print(render_report_markdown(payload), end="")
    elif command == "benchmark" and not args.json:
        print(render_benchmark_summary(payload), end="")
    elif command == "detect-changes" and not args.json:
        print(render_detect_changes_summary(payload), end="")
    elif command == "test-gaps" and not args.json:
        print(render_test_gaps_summary(payload), end="")
    elif command == "review-questions" and not args.json:
        print(render_review_questions_summary(payload), end="")
    elif command == "review-eval" and not args.json:
        print(render_review_eval_summary(payload), end="")
    elif command == "export" and not args.json:
        print(render_export_summary(payload), end="")
    elif command == "flows" and not args.json:
        print(render_flows_summary(payload), end="")
    elif command == "architecture" and not args.json:
        print(render_architecture_summary(payload), end="")
    elif command == "resolvers" and not args.json:
        print(render_resolvers_summary(payload), end="")
    elif command == "communities" and not args.json:
        print(render_communities_summary(payload), end="")
    elif command == "status" and not args.json:
        print(render_status_summary(payload), end="")
    elif command == "postprocess" and not args.json:
        print(render_postprocess_summary(payload), end="")
    elif command == "vulnerabilities" and not args.json:
        print(render_vulnerabilities_summary(payload), end="")
    elif command == "install" and not args.json:
        print(render_install_summary(payload), end="")
    elif command == "registry" and not args.json:
        print(render_registry_summary(payload), end="")
    elif command == "daemon" and not args.json:
        print(render_daemon_summary(payload), end="")
    elif command == "embeddings" and not args.json:
        print(render_embeddings_summary(payload), end="")
    elif command == "path" and not args.json:
        print(render_path_summary(payload), end="")
    elif args.json:
        print(render_json(payload, compact=True))
    elif command == "index":
        print(render_index_summary(payload), end="")
    elif command == "refresh":
        print(render_refresh_summary(payload), end="")
    else:
        print(render_json(payload, compact=False))
    return 0


def _configure_logging(args: argparse.Namespace) -> None:
    level = _log_level_for_args(args)
    logging.basicConfig(
        level=level,
        format=_log_format_for_args(args),
        stream=sys.stderr,
        force=True,
    )
    _configure_dependency_logging(level)


def _log_level_for_args(args: argparse.Namespace) -> int:
    quiet = int(getattr(args, "log_quiet", 0) or 0)
    verbose = int(getattr(args, "log_verbose", 0) or 0)
    if quiet >= 2:
        return logging.ERROR
    if quiet == 1:
        return logging.WARNING
    if verbose >= 2:
        return logging.DEBUG
    if verbose == 1 or _effective_command(args) in {"serve", "watch"}:
        return logging.INFO
    return logging.WARNING


def _log_format_for_args(args: argparse.Namespace) -> str:
    verbose = int(getattr(args, "log_verbose", 0) or 0)
    if _effective_command(args) in {"serve", "watch"} and verbose == 0:
        return "%(levelname)s: %(message)s"
    return "%(levelname)s %(name)s: %(message)s"


def _configure_dependency_logging(level: int) -> None:
    watchfiles_level = logging.NOTSET if level <= logging.DEBUG else logging.WARNING
    logging.getLogger("watchfiles").setLevel(watchfiles_level)
    logging.getLogger("watchfiles.main").setLevel(watchfiles_level)


def _effective_command(args: argparse.Namespace) -> str:
    return args.command


def _add_repo_positional(p: argparse.ArgumentParser) -> None:
    p.add_argument("repo_arg", nargs="?", help="Repository root (default: current directory).")
    p.add_argument("--repo", dest="repo_opt", help="Repository root.")


def _add_db(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db)."
    )


def _add_json(p: argparse.ArgumentParser, *, suppress: bool = False) -> None:
    p.add_argument(
        "--json",
        action="store_true",
        help=argparse.SUPPRESS if suppress else "Emit machine-readable JSON.",
    )


def _add_profile(p: argparse.ArgumentParser, *, default: str | None = "medium") -> None:
    p.add_argument("--profile", choices=PROFILE_CHOICES, default=default)


def _add_logging_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-v",
        "--verbose",
        dest="log_verbose",
        action="count",
        default=0,
        help="Increase diagnostic logging. Repeat for debug logs.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        dest="log_quiet",
        action="count",
        default=0,
        help="Reduce diagnostic logging. Repeat to show errors only.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="csegraph",
        description="SQLite-backed code graph indexing and context retrieval.",
    )
    _add_logging_options(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    index = subparsers.add_parser("index", help="Build a fresh project index.")
    _add_repo_positional(index)
    _add_db(index)
    _add_profile(index)
    index.add_argument(
        "--postprocess",
        choices=["none", "minimal", "full"],
        default="full",
        help="Postprocess level after indexing (default: full).",
    )
    index.add_argument(
        "--exclude",
        action="append",
        default=None,
        metavar="PATTERN",
        help="Extra gitignore-style exclusion (repeatable); applies without editing .csegraphignore.",
    )
    index.add_argument(
        "--include-root",
        action="append",
        default=None,
        metavar="PATH",
        help="Limit indexing to a repo-local subtree. Repeat for monorepo projects.",
    )
    _add_json(index)

    refresh = subparsers.add_parser("refresh", help="Refresh changed files in an index.")
    _add_repo_positional(refresh)
    _add_db(refresh)
    _add_profile(refresh)
    refresh.add_argument(
        "--postprocess",
        choices=["none", "minimal", "full"],
        default="full",
        help="Postprocess level after refresh (default: full).",
    )
    refresh.add_argument(
        "--exclude",
        action="append",
        default=None,
        metavar="PATTERN",
        help="Extra gitignore-style exclusion (repeatable).",
    )
    refresh.add_argument(
        "--include-root",
        action="append",
        default=None,
        metavar="PATH",
        help="Limit refresh to a repo-local subtree. Defaults to indexed include roots.",
    )
    _add_json(refresh)

    context = subparsers.add_parser("context", help="Retrieve graph-backed context.")
    context.add_argument("task_arg", nargs="?", help="Natural-language task.")
    context.add_argument(
        "--repo", default=None, help="Repository root containing the default .csegraph index."
    )
    _add_db(context)
    context.add_argument("--task", default=None, help="Natural-language task.")
    context.add_argument(
        "--target", default=None, help="Optional target node, symbol name, or file path."
    )
    _add_profile(context, default=None)
    context.add_argument(
        "--config", default=None, help="Path to csegraph.json/toml with threshold overrides."
    )
    context.add_argument(
        "--include-source",
        choices=("auto", "always", "never"),
        default="auto",
        help="Control source_text materialization in context nodes (default: auto).",
    )
    context.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Approximate max tokens for returned context nodes.",
    )
    context.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "markdown"),
        default="json",
        help="Output format for context results (default: json).",
    )
    context.add_argument(
        "--explain",
        action="store_true",
        help="Include human-readable explanations for context node selection.",
    )
    context.add_argument(
        "--detail-level",
        choices=("auto", "minimal", "standard", "full"),
        default="auto",
        help="Context detail level: auto starts minimal if sufficient, minimal is compact routing, standard is working context, full includes all explanations.",
    )
    _add_json(context)

    path = subparsers.add_parser("path", help="Find the shortest path between two nodes.")
    path.add_argument("source_arg", nargs="?", help="Source node ID, symbol name, or file path.")
    path.add_argument("target_arg", nargs="?", help="Target node ID, symbol name, or file path.")
    path.add_argument("--source", default=None, help="Source node.")
    path.add_argument("--target", default=None, help="Target node.")
    path.add_argument(
        "--repo", default=None, help="Repository root containing the default .csegraph index."
    )
    path.add_argument(
        "--detail-level",
        choices=["minimal", "standard"],
        default="minimal",
        help="minimal: name chain + length. standard: full nodes/edges.",
    )
    path.add_argument(
        "--relations",
        default=None,
        help="Comma-separated edge kinds to restrict traversal (e.g. 'calls,imports').",
    )
    _add_db(path)
    _add_json(path)

    inspect = subparsers.add_parser("inspect", help="Inspect a graph neighborhood.")
    inspect.add_argument("node_arg", nargs="?", help="Node ID, symbol name, or file path.")
    inspect.add_argument(
        "--repo", default=None, help="Repository root containing the default .csegraph index."
    )
    _add_db(inspect)
    inspect.add_argument("--node", default=None, help="Node ID, symbol name, or file path.")
    inspect.add_argument("--depth", type=int, default=1, help="Neighborhood depth.")
    inspect.add_argument(
        "--detail-level",
        choices=["minimal", "standard"],
        default="minimal",
        help="minimal: summary + top-degree key nodes. standard: full nodes/edges.",
    )
    inspect.add_argument(
        "--relations",
        default=None,
        help="Comma-separated edge kinds to restrict traversal (e.g. 'calls,imports').",
    )
    _add_json(inspect)

    export = subparsers.add_parser(
        "export", help="Export graph visualizations or portable graph data."
    )
    _add_repo_positional(export)
    _add_db(export)
    export.add_argument(
        "--format",
        dest="export_format",
        choices=["html", "tree", "graphml", "obsidian", "json"],
        default="html",
        help="Export format (default: html).",
    )
    export.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output path (file for html/tree/graphml/json, directory for obsidian).",
    )
    _add_json(export)

    install = subparsers.add_parser("install", help="Configure MCP clients to run csegraph serve.")
    _add_repo_positional(install)
    install.add_argument(
        "--platform",
        choices=[
            "auto",
            "codex",
            "cursor",
            "claude-code",
            "gemini-cli",
            "kiro",
            "antigravity-cli",
            "antigravity-ide",
            "copilot",
            "vscode",
        ],
        default="auto",
        help="MCP client platform to configure.",
    )
    install.add_argument(
        "--command",
        dest="server_command",
        default="csegraph",
        help="Executable command used by MCP clients to launch csegraph.",
    )
    install.add_argument(
        "--dry-run", action="store_true", help="Show planned writes without modifying files."
    )
    instructions_group = install.add_mutually_exclusive_group()
    instructions_group.add_argument(
        "--instructions",
        dest="instructions",
        action="store_true",
        default=None,
        help="Generate all agent instruction files (default: platform-scoped guidance).",
    )
    instructions_group.add_argument(
        "--no-instructions",
        dest="instructions",
        action="store_false",
        help="Skip agent instruction files.",
    )
    hooks_group = install.add_mutually_exclusive_group()
    hooks_group.add_argument(
        "--hooks",
        dest="hooks",
        action="store_true",
        default=None,
        help="Install all supported agent hooks (default: platform-scoped hooks).",
    )
    hooks_group.add_argument(
        "--no-hooks",
        dest="hooks",
        action="store_false",
        help="Skip agent hooks.",
    )
    gitignore_group = install.add_mutually_exclusive_group()
    gitignore_group.add_argument(
        "--gitignore",
        dest="gitignore",
        action="store_true",
        default=None,
        help="Add generated local setup paths to .gitignore (default).",
    )
    gitignore_group.add_argument(
        "--no-gitignore",
        dest="gitignore",
        action="store_false",
        help="Do not modify .gitignore.",
    )
    install.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        default=True,
        help="Skip launching the generated MCP server to verify its tool surface.",
    )
    _add_json(install)

    watch = subparsers.add_parser(
        "watch", help="Watch for file changes and auto-refresh the index."
    )
    _add_repo_positional(watch)
    _add_db(watch)
    _add_profile(watch)
    watch.add_argument(
        "--debounce",
        type=int,
        default=500,
        help="Debounce interval in milliseconds (default: 500).",
    )
    _add_json(watch, suppress=True)

    serve = subparsers.add_parser("serve", help="Start the MCP stdio server for coding agents.")
    serve.add_argument(
        "--repo",
        dest="repo_opt",
        default=None,
        help="Bind this MCP server process to a repository root.",
    )
    serve.add_argument(
        "--tools",
        default=None,
        help=(
            "Tools to expose. Use 'core' (default) or a comma-separated subset "
            "of the six core context-engine tool names."
        ),
    )
    serve.add_argument(
        "--platform",
        choices=[
            "codex",
            "cursor",
            "claude-code",
            "gemini-cli",
            "kiro",
            "antigravity-cli",
            "antigravity-ide",
            "copilot",
        ],
        default=None,
        help="Host platform that launched this MCP server.",
    )
    _add_json(serve, suppress=True)

    doctor = subparsers.add_parser("doctor", help="Diagnose CseGraph MCP platform setup.")
    _add_repo_positional(doctor)
    doctor.add_argument(
        "--platform",
        choices=[
            "auto",
            "codex",
            "cursor",
            "claude-code",
            "gemini-cli",
            "kiro",
            "antigravity-cli",
            "antigravity-ide",
            "copilot",
            "vscode",
        ],
        required=True,
        help="MCP client platform to inspect.",
    )
    doctor.add_argument(
        "--command",
        dest="server_command",
        default="csegraph",
        help="Executable command to resolve when config is missing.",
    )
    doctor.add_argument(
        "--require-observed-call",
        action="store_true",
        help="Report pending_host_approval until a real MCP tool call has been observed locally.",
    )
    doctor.add_argument("--format", choices=["json"], default=None, help="Output format.")
    doctor.add_argument("--no-verify", dest="verify", action="store_false", default=True)
    _add_json(doctor)

    lsp = subparsers.add_parser("lsp", help="Start the LSP stdio server for editors.")
    _add_repo_positional(lsp)
    _add_db(lsp)
    _add_json(lsp, suppress=True)

    status = subparsers.add_parser("status", help="Show graph health and staleness info.")
    _add_repo_positional(status)
    status.add_argument(
        "--verbose", action="store_true", help="Include extra detail (parse error paths)."
    )
    _add_db(status)
    _add_json(status)

    postprocess = subparsers.add_parser(
        "postprocess", help="Rebuild FTS and communities without re-parsing."
    )
    _add_repo_positional(postprocess)
    _add_db(postprocess)
    postprocess.add_argument(
        "--level",
        choices=["none", "minimal", "full"],
        default="full",
        help="Postprocess level: none (skip all), minimal (FTS only), full (FTS + communities). Default: full.",
    )
    postprocess.add_argument("--no-fts", action="store_true", help="Skip FTS rebuild.")
    postprocess.add_argument(
        "--no-communities", action="store_true", help="Skip community detection."
    )
    _add_json(postprocess)

    analyze = subparsers.add_parser(
        "analyze", help="Run one ranked diagnostics summary for the indexed repo."
    )
    _add_repo_positional(analyze)
    _add_db(analyze)
    analyze.add_argument(
        "--base-ref", default="HEAD~1", help="Git ref to diff against (default: HEAD~1)."
    )
    analyze.add_argument(
        "--limit", type=int, default=10, help="Max items per section (default: 10)."
    )
    _add_json(analyze)

    registry = subparsers.add_parser(
        "registry", help="Manage the multi-repo registry (~/.csegraph/registry.json)."
    )
    reg_sub = registry.add_subparsers(dest="registry_command", required=True)

    reg_register = reg_sub.add_parser("register", help="Register a repository.")
    reg_register.add_argument("repo_arg", nargs="?", help="Repository root to register.")
    reg_register.add_argument("--repo", dest="repo_opt", help="Repository root to register.")
    reg_register.add_argument(
        "--alias", default=None, help="Short alias (default: directory name)."
    )
    reg_register.add_argument("--db", default=None, help="SQLite database path.")
    _add_profile(reg_register)
    _add_json(reg_register)

    reg_unregister = reg_sub.add_parser("unregister", help="Remove a repository from the registry.")
    reg_unregister.add_argument("alias", help="Alias of the repo to remove.")
    _add_json(reg_unregister)

    reg_list = reg_sub.add_parser("list", help="List all registered repositories.")
    _add_json(reg_list)

    reg_status = reg_sub.add_parser("status", help="Show detailed status for a registered repo.")
    reg_status.add_argument("alias", help="Alias of the repo to inspect.")
    _add_json(reg_status)

    daemon = subparsers.add_parser("daemon", help="Manage multi-repo watch daemon processes.")
    daemon_sub = daemon.add_subparsers(dest="daemon_command", required=True)

    daemon_start = daemon_sub.add_parser(
        "start", help="Start watch processes for registered repos."
    )
    daemon_start.add_argument(
        "--alias",
        action="append",
        default=None,
        help="Restrict to specific alias(es). May be repeated.",
    )
    daemon_start.add_argument(
        "--profile", default=None, help="Override watch profile for all repos."
    )
    _add_json(daemon_start)

    daemon_stop = daemon_sub.add_parser("stop", help="Stop watch processes.")
    daemon_stop.add_argument(
        "--alias",
        action="append",
        default=None,
        help="Restrict to specific alias(es). May be repeated.",
    )
    _add_json(daemon_stop)

    daemon_status = daemon_sub.add_parser("status", help="Show status of watch processes.")
    _add_json(daemon_status)

    return parser


def _add_benchmark_command(subparsers: argparse._SubParsersAction) -> None:
    benchmark = subparsers.add_parser(
        "benchmark",
        help="Benchmark index, context, token reduction, corpus quality, or agent workflows.",
    )
    _add_repo_positional(benchmark)
    _add_db(benchmark)
    _add_profile(benchmark)
    benchmark.add_argument(
        "--corpus",
        default=None,
        help="Path to a context quality benchmark corpus JSON file.",
    )
    benchmark.add_argument(
        "--query", default="Benchmark context retrieval", help="Context query to benchmark."
    )
    benchmark.add_argument("--target", default=None, help="Optional context target symbol.")
    benchmark.add_argument(
        "--expect-node",
        action="append",
        default=None,
        help="Expected context node ID for benchmark quality checks. May be repeated.",
    )
    benchmark.add_argument(
        "--agent-workflows",
        action="store_true",
        help="Run multi-step agent workflow benchmarks (minimal → context → optional graph).",
    )
    _add_json(benchmark)


def _build_dev_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools/csegraph_dev.py",
        description="Repo-local maintainer tooling for CseGraph diagnostics and experiments.",
    )
    _add_logging_options(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    architecture = subparsers.add_parser(
        "architecture", help="Community summaries and architecture overview."
    )
    _add_repo_positional(architecture)
    _add_db(architecture)
    architecture.add_argument(
        "--limit", type=int, default=20, help="Max community summaries (default: 20)."
    )
    _add_json(architecture)

    flows = subparsers.add_parser(
        "flows", help="Trace execution flows from entry points through the call graph."
    )
    _add_repo_positional(flows)
    _add_db(flows)
    flows.add_argument(
        "--entry-point",
        default=None,
        help="Trace from a specific function or symbol instead of auto-detecting entry points.",
    )
    flows.add_argument(
        "--max-depth",
        type=int,
        default=10,
        help="Maximum BFS depth for flow tracing (default: 10).",
    )
    flows.add_argument(
        "--limit", type=int, default=20, help="Maximum number of flows to return (default: 20)."
    )
    _add_json(flows)

    resolvers = subparsers.add_parser(
        "resolvers",
        help="Run resolver passes to add inferred edges (transitive tests, imports, TS aliases).",
    )
    _add_repo_positional(resolvers)
    _add_db(resolvers)
    _add_json(resolvers)

    communities = subparsers.add_parser(
        "communities", help="Detect communities in the dependency graph."
    )
    _add_repo_positional(communities)
    _add_db(communities)
    _add_json(communities)

    report = subparsers.add_parser("report", help="Generate a project report from the index.")
    _add_repo_positional(report)
    _add_db(report)
    _add_json(report)

    detect_changes = subparsers.add_parser(
        "detect-changes", help="Detect changed symbols and score review risk."
    )
    _add_repo_positional(detect_changes)
    _add_db(detect_changes)
    detect_changes.add_argument(
        "--base-ref", default="HEAD~1", help="Git ref to diff against (default: HEAD~1)."
    )
    _add_json(detect_changes)

    test_gaps = subparsers.add_parser(
        "test-gaps", help="Report untested symbols and coverage hotspots."
    )
    _add_repo_positional(test_gaps)
    _add_db(test_gaps)
    test_gaps.add_argument(
        "--limit", type=int, default=20, help="Max hotspots to show (default: 20)."
    )
    _add_json(test_gaps)

    review_qs = subparsers.add_parser(
        "review-questions", help="Generate review questions from graph structure."
    )
    _add_repo_positional(review_qs)
    _add_db(review_qs)
    review_qs.add_argument(
        "--base-ref", default="HEAD~1", help="Git ref to diff against (default: HEAD~1)."
    )
    _add_json(review_qs)

    review_eval = subparsers.add_parser(
        "review-eval", help="Evaluate review intelligence against ground truth."
    )
    _add_repo_positional(review_eval)
    _add_db(review_eval)
    review_eval.add_argument(
        "--base-ref", default="HEAD~1", help="Git ref to diff against (default: HEAD~1)."
    )
    review_eval.add_argument(
        "--ground-truth", required=True, help="Comma-separated node IDs or path to JSON file."
    )
    review_eval.add_argument(
        "--risk-threshold",
        choices=["high", "medium", "low"],
        default="medium",
        help="Detection threshold (default: medium).",
    )
    _add_json(review_eval)

    vulns = subparsers.add_parser(
        "vulnerabilities", help="Scan for security vulnerabilities using the dependency graph."
    )
    _add_repo_positional(vulns)
    _add_db(vulns)
    vulns.add_argument(
        "--limit", type=int, default=50, help="Max vulnerabilities per severity (default: 50)."
    )
    _add_json(vulns)

    # -- embeddings --
    embeddings = subparsers.add_parser(
        "embeddings", help="Compute, search, or manage code embeddings (optional, local-first)."
    )
    emb_sub = embeddings.add_subparsers(dest="embeddings_command", required=True)

    emb_compute = emb_sub.add_parser("compute", help="Compute embeddings for symbol nodes.")
    _add_repo_positional(emb_compute)
    _add_db(emb_compute)
    emb_compute.add_argument(
        "--model", default=None, help="Embedding model name (default: all-MiniLM-L6-v2 for local)."
    )
    emb_compute.add_argument(
        "--provider",
        choices=["local", "openai-compatible"],
        default="local",
        help="Embedding provider (default: local).",
    )
    emb_compute.add_argument("--endpoint", default=None, help="OpenAI-compatible API endpoint URL.")
    _add_json(emb_compute)

    emb_search = emb_sub.add_parser("search", help="Semantic search over embedded symbols.")
    _add_repo_positional(emb_search)
    _add_db(emb_search)
    emb_search.add_argument("query", help="Natural-language search query.")
    emb_search.add_argument(
        "--top-k", type=int, default=10, help="Number of results (default: 10)."
    )
    emb_search.add_argument(
        "--no-hybrid",
        action="store_true",
        help="Disable hybrid FTS fusion; use embedding-only search.",
    )
    emb_search.add_argument("--model", default=None, help="Embedding model name.")
    emb_search.add_argument(
        "--provider",
        choices=["local", "openai-compatible"],
        default="local",
        help="Embedding provider.",
    )
    emb_search.add_argument("--endpoint", default=None, help="OpenAI-compatible API endpoint URL.")
    _add_json(emb_search)

    emb_status = emb_sub.add_parser("status", help="Show embedding cache statistics.")
    _add_repo_positional(emb_status)
    _add_db(emb_status)
    emb_status.add_argument("--model", default=None, help="Embedding model name.")
    emb_status.add_argument(
        "--provider",
        choices=["local", "openai-compatible"],
        default="local",
        help="Embedding provider.",
    )
    _add_json(emb_status)

    emb_clear = emb_sub.add_parser(
        "clear", help="Clear the embedding cache for the current provider/model."
    )
    _add_repo_positional(emb_clear)
    _add_db(emb_clear)
    emb_clear.add_argument("--model", default=None, help="Embedding model name.")
    emb_clear.add_argument(
        "--provider",
        choices=["local", "openai-compatible"],
        default="local",
        help="Embedding provider.",
    )
    _add_json(emb_clear)

    _add_benchmark_command(subparsers)

    return parser


def _dispatch(args: argparse.Namespace) -> Any:
    if args.command == "index":
        from csegraph._core.index.services import IndexService
        from csegraph._core.postprocess import PostprocessService

        repo = _repo_arg(args)
        db = _db_arg(args, repo)
        index_result = IndexService(db).index(
            repo,
            profile=args.profile,
            exclude_patterns=getattr(args, "exclude", None),
            include_roots=getattr(args, "include_root", None),
        )
        pp_level = getattr(args, "postprocess", "full")
        pp_result = None
        skipped_reason = None
        if pp_level != "none":
            pp_result = PostprocessService(db).postprocess(level=pp_level)
        else:
            skipped_reason = "disabled"
        attach_postprocess_metadata(index_result, db, pp_level, pp_result, skipped_reason)
        return index_result
    if args.command == "refresh":
        from csegraph._core.index.services import RefreshService
        from csegraph._core.postprocess import PostprocessService

        repo = _repo_arg(args)
        db = _db_arg(args, repo)
        refresh_result = RefreshService(db).refresh(
            profile=args.profile,
            exclude_patterns=getattr(args, "exclude", None),
            include_roots=getattr(args, "include_root", None),
        )
        pp_level = getattr(args, "postprocess", "full")
        pp_result = None
        skipped_reason = None
        if pp_level != "none" and refresh_result.files_indexed > 0:
            pp_result = PostprocessService(db).postprocess(level=pp_level)
        elif pp_level == "none":
            skipped_reason = "disabled"
        else:
            skipped_reason = "unchanged"
        attach_postprocess_metadata(refresh_result, db, pp_level, pp_result, skipped_reason)
        return refresh_result
    if args.command == "context":
        from csegraph._core.retrieval.context import ContextService

        repo_path = Path(args.repo or ".").resolve()
        task = args.task or args.task_arg
        if not task:
            raise ValueError('context requires a task. Example: csegraph context "Fix auth"')
        return ContextService(_db_arg(args, str(repo_path))).build_context(
            task=task,
            target=args.target,
            profile=args.profile,
            include_source=args.include_source,
            max_tokens=args.max_tokens,
            explain=args.explain,
            config_path=args.config,
            detail_level=args.detail_level,
        )
    if args.command == "path":
        from csegraph._core.graph.queries import GraphQueryService

        repo_path = Path(args.repo or ".").resolve()
        source = args.source or args.source_arg
        target = args.target or args.target_arg
        if not source or not target:
            raise ValueError("path requires two nodes. Example: csegraph path greet main")
        relations = (
            [r.strip() for r in args.relations.split(",") if r.strip()] if args.relations else None
        )
        return GraphQueryService(_db_arg(args, str(repo_path))).shortest_path(
            source, target, detail_level=args.detail_level, relations=relations
        )
    if args.command == "inspect":
        from csegraph._core.graph.queries import GraphQueryService

        repo_path = Path(args.repo or ".").resolve()
        node = args.node or args.node_arg
        if not node:
            raise ValueError("inspect requires a node. Example: csegraph inspect MyClass.method")
        relations = (
            [r.strip() for r in args.relations.split(",") if r.strip()] if args.relations else None
        )
        graph_result = GraphQueryService(_db_arg(args, str(repo_path))).neighborhood(
            node,
            depth=args.depth,
            detail_level=args.detail_level,
            relations=relations,
        )
        graph_result.command = "inspect"
        return graph_result
    if args.command == "export":
        from csegraph._core.core.models import ExportResult
        from csegraph._core.graph.exports import ExportService

        repo = _repo_arg(args)
        db_path = _db_arg(args, repo)
        fmt = args.export_format
        if fmt == "html":
            from csegraph._core.graph.visual import VisualExportService

            out = args.output or _default_graph_output_path(db_path)
            visual = VisualExportService(db_path).export(out)
            return ExportResult(
                command="export",
                db_path=visual.db_path,
                repo_root=visual.repo_root,
                output_path=visual.output_path,
                format="html",
                total_nodes=visual.total_nodes,
                total_edges=visual.total_edges,
                files_written=1,
            )
        if fmt == "tree":
            from csegraph._core.graph.tree import TreeExportService

            out = args.output or str(Path(db_path).resolve().with_name("csegraph-tree.html"))
            visual = TreeExportService(db_path).export(out)
            return ExportResult(
                command="export",
                db_path=visual.db_path,
                repo_root=visual.repo_root,
                output_path=visual.output_path,
                format="tree",
                total_nodes=visual.total_nodes,
                total_edges=visual.total_edges,
                files_written=1,
            )
        if args.output:
            out = args.output
        elif fmt == "obsidian":
            out = str(Path(db_path).resolve().with_name("csegraph-vault"))
        elif fmt == "json":
            out = str(Path(db_path).resolve().with_name("csegraph-export.json"))
        else:
            out = str(Path(db_path).resolve().with_name("csegraph-graph.graphml"))
        return ExportService(db_path).export(out, fmt=fmt)
    if args.command == "analyze":
        repo = _repo_arg(args)
        return _run_analyze(repo, _db_arg(args, repo), base_ref=args.base_ref, limit=args.limit)
    if args.command == "architecture":
        from csegraph._core.graph.architecture import ArchitectureService

        repo = _repo_arg(args)
        return ArchitectureService(_db_arg(args, repo)).overview(limit=args.limit)
    if args.command == "flows":
        from csegraph._core.graph.flows import FlowService

        repo = _repo_arg(args)
        return FlowService(_db_arg(args, repo)).trace(
            entry_point=args.entry_point,
            max_depth=args.max_depth,
            limit=args.limit,
        )
    if args.command == "resolvers":
        from csegraph._core.graph.resolvers import ResolverService

        repo = _repo_arg(args)
        return ResolverService(_db_arg(args, repo)).run_all()
    if args.command == "communities":
        from csegraph._core.graph.communities import detect_communities

        repo = _repo_arg(args)
        return detect_communities(_db_arg(args, repo))
    if args.command == "install":
        from csegraph._core.mcp_install import McpInstallService

        repo = _repo_arg(args)
        return McpInstallService(repo, command=args.server_command).install(
            platform=args.platform,
            dry_run=args.dry_run,
            instructions=args.instructions,
            hooks=args.hooks,
            gitignore=args.gitignore,
            verify=args.verify,
        )
    if args.command == "doctor":
        from csegraph._core.mcp_doctor import McpDoctorService

        repo = _repo_arg(args)
        service = McpDoctorService(repo, command=args.server_command)
        if args.platform == "auto":
            return service.doctor_all(
                require_observed_call=args.require_observed_call,
                verify=args.verify,
            )
        return service.doctor(
            platform=args.platform,
            require_observed_call=args.require_observed_call,
            verify=args.verify,
        )
    if args.command == "report":
        from csegraph._core.graph.report import ReportService

        repo = _repo_arg(args)
        return ReportService(_db_arg(args, repo)).report()
    if args.command == "watch":
        from csegraph._core.watch import watch as run_watch

        repo = _repo_arg(args)
        run_watch(repo, _db_arg(args, repo), profile=args.profile, debounce_ms=args.debounce)
        return None
    if args.command == "serve":
        import asyncio

        from csegraph._core.server import run_stdio

        raw = args.tools
        if raw is None or raw == "core":
            allowed = None
        else:
            allowed = [t.strip() for t in raw.split(",") if t.strip()]
        if raw is not None and not allowed:
            raise SystemExit(
                "error: --tools resolved to an empty list. Use 'core' or a comma-separated list of tool names."
            )
        bound_repo = str(Path(args.repo_opt).resolve()) if args.repo_opt else None
        asyncio.run(
            run_stdio(
                allowed_tools=allowed,
                bound_repo=bound_repo,
                host_platform=args.platform,
            )
        )
        return None
    if args.command == "lsp":
        from csegraph._core.lsp import run_stdio_lsp

        repo = _repo_arg(args)
        return_code = run_stdio_lsp(repo, _db_arg(args, repo))
        if return_code:
            raise SystemExit(return_code)
        return None
    if args.command == "status":
        from csegraph._core.status import StatusService

        repo = _repo_arg(args)
        return StatusService(_db_arg(args, repo)).status(verbose=args.verbose)
    if args.command == "postprocess":
        from csegraph._core.postprocess import PostprocessService

        repo = _repo_arg(args)
        return PostprocessService(_db_arg(args, repo)).postprocess(
            level=args.level,
            no_fts=args.no_fts,
            no_communities=args.no_communities,
        )
    if args.command == "detect-changes":
        from csegraph._core.graph.change_detection import ChangeDetectionService

        repo = _repo_arg(args)
        return ChangeDetectionService(_db_arg(args, repo)).detect_changes(base_ref=args.base_ref)
    if args.command == "test-gaps":
        from csegraph._core.graph.test_gaps import TestGapService

        repo = _repo_arg(args)
        return TestGapService(_db_arg(args, repo)).analyze(limit=args.limit)
    if args.command == "review-questions":
        from csegraph._core.graph.review_questions import ReviewQuestionsService

        repo = _repo_arg(args)
        return ReviewQuestionsService(_db_arg(args, repo)).generate(base_ref=args.base_ref)
    if args.command == "review-eval":
        import json as _json

        from csegraph._core.graph.review_eval import ReviewEvalService

        repo = _repo_arg(args)
        gt = args.ground_truth
        if Path(gt).exists():
            ground_truth_ids = _json.loads(Path(gt).read_text(encoding="utf-8"))
        else:
            ground_truth_ids = [s.strip() for s in gt.split(",") if s.strip()]
        return ReviewEvalService(_db_arg(args, repo)).evaluate(
            ground_truth_ids=ground_truth_ids,
            base_ref=args.base_ref,
            risk_threshold=args.risk_threshold,
        )
    if args.command == "vulnerabilities":
        from csegraph._core.graph.vulnerabilities import VulnerabilityService

        repo = _repo_arg(args)
        return VulnerabilityService(_db_arg(args, repo)).scan(limit=args.limit)
    if args.command == "embeddings":
        from csegraph._core.graph.embeddings import EmbeddingService

        repo = _repo_arg(args)
        db = _db_arg(args, repo)
        embedding_service = EmbeddingService(
            db,
            model=getattr(args, "model", None),
            provider=getattr(args, "provider", "local"),
            endpoint=getattr(args, "endpoint", None),
        )
        if args.embeddings_command == "compute":
            return embedding_service.compute()
        if args.embeddings_command == "search":
            return embedding_service.search(
                args.query,
                top_k=args.top_k,
                hybrid=not args.no_hybrid,
            )
        if args.embeddings_command == "status":
            return embedding_service.status()
        if args.embeddings_command == "clear":
            return embedding_service.clear()
        raise ValueError(f"Unknown embeddings subcommand: {args.embeddings_command}")
    if args.command == "benchmark":
        from csegraph._core.benchmark import BenchmarkService

        repo = _repo_arg(args)
        db_path = _db_arg(args, repo)
        if getattr(args, "corpus", None):
            return BenchmarkService(db_path).run_corpus(
                repo,
                args.corpus,
                profile=args.profile,
            )
        if getattr(args, "agent_workflows", False):
            return BenchmarkService(db_path).run_agent_workflows(
                repo,
                profile=args.profile,
            )
        return BenchmarkService(db_path).run(
            repo,
            profile=args.profile,
            query=args.query,
            target=args.target,
            graph_output_path=_default_graph_output_path(db_path),
            expected_nodes=args.expect_node,
        )
    if args.command == "registry":
        from csegraph._core.registry import RegistryService

        registry_service = RegistryService()
        if args.registry_command == "register":
            repo = _repo_arg(args)
            return registry_service.register(
                repo,
                alias=args.alias,
                profile=args.profile,
                db=args.db,
            )
        if args.registry_command == "unregister":
            return registry_service.unregister(args.alias)
        if args.registry_command == "list":
            return registry_service.list()
        if args.registry_command == "status":
            return registry_service.status(args.alias)
        raise ValueError(f"Unknown registry subcommand: {args.registry_command}")
    if args.command == "daemon":
        from csegraph._core.daemon import DaemonService

        daemon_service = DaemonService()
        if args.daemon_command == "start":
            return daemon_service.start(
                aliases=args.alias,
                profile=args.profile,
            )
        if args.daemon_command == "stop":
            return daemon_service.stop(aliases=args.alias)
        if args.daemon_command == "status":
            return daemon_service.status()
        raise ValueError(f"Unknown daemon subcommand: {args.daemon_command}")
    raise ValueError(f"Unknown command: {args.command}")


def _run_analyze(repo: str, db: str, *, base_ref: str, limit: int) -> dict[str, Any]:
    sections: list[dict[str, Any]] = []
    warnings: list[str] = []

    def add_section(name: str, runner: Any, summary_key: str = "summary") -> None:
        try:
            payload = to_dict(runner())
            section_warnings = payload.get("warnings", [])
            sections.append(
                {
                    "name": name,
                    "status": "ok",
                    "summary": payload.get(summary_key, ""),
                    "data": payload,
                }
            )
            warnings.extend(f"{name}: {warning}" for warning in section_warnings)
        except Exception as exc:
            message = str(exc)
            sections.append(
                {
                    "name": name,
                    "status": "error",
                    "summary": message,
                    "data": {},
                }
            )
            warnings.append(f"{name}: {message}")

    def changes() -> Any:
        from csegraph._core.graph.change_detection import ChangeDetectionService

        return ChangeDetectionService(db).detect_changes(base_ref=base_ref)

    def test_gaps() -> Any:
        from csegraph._core.graph.test_gaps import TestGapService

        return TestGapService(db).analyze(limit=limit)

    def architecture() -> Any:
        from csegraph._core.graph.architecture import ArchitectureService

        return ArchitectureService(db).overview(limit=limit)

    def flows() -> Any:
        from csegraph._core.graph.flows import FlowService

        return FlowService(db).trace(limit=limit)

    def security() -> Any:
        from csegraph._core.graph.vulnerabilities import VulnerabilityService

        return VulnerabilityService(db).scan(limit=limit)

    add_section("changes", changes)
    add_section("test_gaps", test_gaps)
    add_section("architecture", architecture)
    add_section("flows", flows)
    add_section("security", security)

    next_actions = _analyze_next_actions(sections)
    return {
        "command": "analyze",
        "db_path": db,
        "repo_root": repo,
        "base_ref": base_ref,
        "sections": sections,
        "next_actions": next_actions,
        "warnings": warnings,
    }


def _analyze_next_actions(sections: list[dict[str, Any]]) -> list[dict[str, str]]:
    by_name = {section["name"]: section for section in sections}
    actions: list[dict[str, str]] = []

    changes = by_name.get("changes", {}).get("data", {})
    high_risk = changes.get("high_risk", [])
    medium_risk = changes.get("medium_risk", [])
    if high_risk:
        actions.append(
            {
                "action": "inspect_high_risk_change",
                "command": f"csegraph inspect {high_risk[0]['id']}",
                "reason": "High-risk changed symbol found.",
            }
        )
    elif medium_risk:
        actions.append(
            {
                "action": "inspect_medium_risk_change",
                "command": f"csegraph inspect {medium_risk[0]['id']}",
                "reason": "Medium-risk changed symbol found.",
            }
        )

    test_gaps = by_name.get("test_gaps", {}).get("data", {})
    hotspots = test_gaps.get("hotspots", [])
    if hotspots:
        actions.append(
            {
                "action": "add_test_coverage",
                "command": f'csegraph context "Add tests for {hotspots[0]["name"]}" --target {hotspots[0]["id"]}',
                "reason": "Untested hotspot found.",
            }
        )

    architecture = by_name.get("architecture", {}).get("data", {})
    if architecture.get("warnings"):
        actions.append(
            {
                "action": "review_architecture_warning",
                "command": "csegraph analyze",
                "reason": architecture["warnings"][0],
            }
        )

    if not actions:
        actions.append(
            {
                "action": "retrieve_task_context",
                "command": 'csegraph context "<task>"',
                "reason": "No urgent diagnostics found; start with task-specific context.",
            }
        )
    return actions


def _validate_cli_options(args: argparse.Namespace) -> None:
    if (
        getattr(args, "command", None) == "context"
        and getattr(args, "json", False)
        and getattr(args, "output_format", "json") == "markdown"
    ):
        raise CsegraphCLIError(
            "--json cannot be combined with --format markdown",
            error_code="invalid_cli_options",
        )


def _repo_arg(args: argparse.Namespace) -> str:
    return str(Path(args.repo_opt or args.repo_arg or ".").resolve())


def _db_arg(args: argparse.Namespace, repo: str) -> str:
    repo_path = Path(repo).resolve()
    if args.db:
        db_path = assert_safe_db_path(args.db, repo_path, "Database")
        return str(db_path)
    return str(repo_path / ".csegraph" / "index.db")


def _default_graph_output_path(db_path: str) -> str:
    return str(Path(db_path).resolve().with_name("csegraph-graph.html"))
