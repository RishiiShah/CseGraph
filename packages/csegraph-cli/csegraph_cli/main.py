"""csegraph_cli.main - CLI entrypoint for csegraph.

Provides the `csegraph` shell command (registered via pyproject_cli.toml).
Importable standalone; depends on csegraph-core.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from csegraph_core.config.profiles import PROFILES
from csegraph_core.core.models import to_dict
from csegraph_cli.errors import CsegraphCLIError, error_payload
from csegraph_cli.renderer import (
    render_communities_summary,
    render_context_markdown,
    render_benchmark_summary,
    render_hooks_summary,
    render_index_summary,
    render_json,
    render_path_summary,
    render_postprocess_summary,
    render_refresh_summary,
    render_report_markdown,
    render_status_summary,
    render_visual_export_summary,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        _validate_cli_options(args)
        result = _dispatch(args)
    except Exception as exc:
        print(json.dumps(error_payload(exc), indent=2, sort_keys=True), file=sys.stderr)
        return 1

    if result is None:
        return 0
    payload = to_dict(result)
    if args.command == "context" and args.output_format == "markdown":
        print(render_context_markdown(payload), end="")
    elif args.command == "report" and not args.json:
        print(render_report_markdown(payload), end="")
    elif args.command == "graph" and not args.json:
        print(render_visual_export_summary(payload), end="")
    elif args.command == "tree" and not args.json:
        print(render_visual_export_summary(payload), end="")
    elif args.command == "benchmark" and not args.json:
        print(render_benchmark_summary(payload), end="")
    elif args.command == "communities" and not args.json:
        print(render_communities_summary(payload), end="")
    elif args.command == "status" and not args.json:
        print(render_status_summary(payload), end="")
    elif args.command == "postprocess" and not args.json:
        print(render_postprocess_summary(payload), end="")
    elif args.command == "hooks" and not args.json:
        print(render_hooks_summary(payload), end="")
    elif args.command == "path" and not args.json:
        print(render_path_summary(payload), end="")
    elif args.json:
        print(render_json(payload, compact=True))
    elif args.command == "index":
        print(render_index_summary(payload), end="")
    elif args.command == "refresh":
        print(render_refresh_summary(payload), end="")
    else:
        print(render_json(payload, compact=False))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="csegraph",
        description="SQLite-backed code graph indexing and context retrieval.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index = subparsers.add_parser("index", help="Build a fresh project index.")
    index.add_argument("repo_arg", nargs="?", help="Repository root to index (default: current directory).")
    index.add_argument("--repo", dest="repo_opt", help="Repository root to index.")
    index.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    index.add_argument("--profile", choices=sorted(PROFILES), default="medium")
    index.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    refresh = subparsers.add_parser("refresh", help="Refresh changed files in an index.")
    refresh.add_argument("repo_arg", nargs="?", help="Repository root containing the default .csegraph index.")
    refresh.add_argument("--repo", dest="repo_opt", help="Repository root containing the default .csegraph index.")
    refresh.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    refresh.add_argument("--profile", choices=sorted(PROFILES), default="medium")
    refresh.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    context = subparsers.add_parser("context", help="Retrieve graph-backed context.")
    context.add_argument("task_arg", nargs="?", help="Natural-language task.")
    context.add_argument("--repo", default=None, help="Repository root containing the default .csegraph index.")
    context.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    context.add_argument("--task", default=None, help="Natural-language task.")
    context.add_argument("--target", default=None, help="Optional target node, symbol name, or file path.")
    context.add_argument("--profile", choices=sorted(PROFILES), default=None)
    context.add_argument("--config", default=None, help="Path to csegraph.json/toml with threshold overrides.")
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
    context.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    path = subparsers.add_parser("path", help="Find the shortest path between two nodes.")
    path.add_argument("source_arg", nargs="?", help="Source node ID, symbol name, or file path.")
    path.add_argument("target_arg", nargs="?", help="Target node ID, symbol name, or file path.")
    path.add_argument("--source", default=None, help="Source node.")
    path.add_argument("--target", default=None, help="Target node.")
    path.add_argument("--repo", default=None, help="Repository root containing the default .csegraph index.")
    path.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    path.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    inspect = subparsers.add_parser("inspect", help="Inspect a graph neighborhood.")
    inspect.add_argument("node_arg", nargs="?", help="Node ID, symbol name, or file path.")
    inspect.add_argument("--repo", default=None, help="Repository root containing the default .csegraph index.")
    inspect.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    inspect.add_argument("--node", default=None, help="Node ID, symbol name, or file path.")
    inspect.add_argument("--depth", type=int, default=1, help="Neighborhood depth.")
    inspect.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    graph = subparsers.add_parser("graph", help="Export a visual HTML graph.")
    graph.add_argument("--repo", default=None, help="Repository root containing the default .csegraph index.")
    graph.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    graph.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output HTML file path (default: beside the SQLite index DB).",
    )
    graph.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    tree = subparsers.add_parser("tree", help="Export an interactive HTML file tree visualization.")
    tree.add_argument("--repo", default=None, help="Repository root containing the default .csegraph index.")
    tree.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    tree.add_argument(
        "--output", "-o", default=None,
        help="Output HTML file path (default: beside the SQLite index DB).",
    )
    tree.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    communities = subparsers.add_parser("communities", help="Detect communities in the dependency graph.")
    communities.add_argument("repo_arg", nargs="?", help="Repository root containing the default .csegraph index.")
    communities.add_argument("--repo", dest="repo_opt", help="Repository root.")
    communities.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    communities.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    hooks = subparsers.add_parser("hooks", help="Manage csegraph git hooks.")
    hooks_sub = hooks.add_subparsers(dest="hooks_command", required=True)
    hooks_install = hooks_sub.add_parser("install", help="Install post-commit/merge/checkout hooks.")
    hooks_install.add_argument("repo_arg", nargs="?", help="Repository root (default: current directory).")
    hooks_install.add_argument("--repo", dest="repo_opt", help="Repository root.")
    hooks_install.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    hooks_uninstall = hooks_sub.add_parser("uninstall", help="Remove csegraph git hooks.")
    hooks_uninstall.add_argument("repo_arg", nargs="?", help="Repository root (default: current directory).")
    hooks_uninstall.add_argument("--repo", dest="repo_opt", help="Repository root.")
    hooks_uninstall.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    report = subparsers.add_parser("report", help="Generate a project report from the index.")
    report.add_argument("repo_arg", nargs="?", help="Repository root containing the default .csegraph index.")
    report.add_argument("--repo", dest="repo_opt", help="Repository root containing the default .csegraph index.")
    report.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    report.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    watch = subparsers.add_parser("watch", help="Watch for file changes and auto-refresh the index.")
    watch.add_argument("repo_arg", nargs="?", help="Repository root to watch (default: current directory).")
    watch.add_argument("--repo", dest="repo_opt", help="Repository root to watch.")
    watch.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    watch.add_argument("--profile", choices=sorted(PROFILES), default="medium")
    watch.add_argument("--debounce", type=int, default=500, help="Debounce interval in milliseconds (default: 500).")
    watch.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    serve = subparsers.add_parser("serve", help="Start the MCP stdio server for coding agents.")
    serve.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    status = subparsers.add_parser("status", help="Show graph health and staleness info.")
    status.add_argument("repo_arg", nargs="?", help="Repository root (default: current directory).")
    status.add_argument("--repo", dest="repo_opt", help="Repository root.")
    status.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    status.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    status.add_argument("--verbose", action="store_true", help="Include extra detail (parse error paths).")

    postprocess = subparsers.add_parser("postprocess", help="Rebuild FTS and communities without re-parsing.")
    postprocess.add_argument("repo_arg", nargs="?", help="Repository root (default: current directory).")
    postprocess.add_argument("--repo", dest="repo_opt", help="Repository root.")
    postprocess.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    postprocess.add_argument("--no-fts", action="store_true", help="Skip FTS rebuild.")
    postprocess.add_argument("--no-communities", action="store_true", help="Skip community detection.")
    postprocess.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    benchmark = subparsers.add_parser("benchmark", help="Time index, context, graph, and report.")
    benchmark.add_argument("repo_arg", nargs="?", help="Repository root to benchmark (default: current directory).")
    benchmark.add_argument("--repo", dest="repo_opt", help="Repository root to benchmark.")
    benchmark.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    benchmark.add_argument("--profile", choices=sorted(PROFILES), default="medium")
    benchmark.add_argument("--query", default="Benchmark context retrieval", help="Context query to benchmark.")
    benchmark.add_argument("--target", default=None, help="Optional context target symbol.")
    benchmark.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    return parser


def _dispatch(args: argparse.Namespace) -> Any:
    if args.command == "index":
        from csegraph_core.index.services import IndexService
        repo = _repo_arg(args)
        return IndexService(_db_arg(args, repo)).index(repo, profile=args.profile)
    if args.command == "refresh":
        from csegraph_core.index.services import RefreshService
        repo = _repo_arg(args)
        return RefreshService(_db_arg(args, repo)).refresh(profile=args.profile)
    if args.command == "context":
        from csegraph_core.retrieval.context import ContextService
        repo = Path(args.repo or ".").resolve()
        task = args.task or args.task_arg
        if not task:
            raise ValueError('context requires a task. Example: csegraph context "Fix auth"')
        return ContextService(_db_arg(args, str(repo))).build_context(
            task=task,
            target=args.target,
            profile=args.profile,
            include_source=args.include_source,
            max_tokens=args.max_tokens,
            explain=args.explain,
            config_path=args.config,
        )
    if args.command == "path":
        from csegraph_core.graph.queries import GraphQueryService
        repo = Path(args.repo or ".").resolve()
        source = args.source or args.source_arg
        target = args.target or args.target_arg
        if not source or not target:
            raise ValueError("path requires two nodes. Example: csegraph path greet main")
        return GraphQueryService(_db_arg(args, str(repo))).shortest_path(source, target)
    if args.command == "inspect":
        from csegraph_core.graph.queries import GraphQueryService
        repo = Path(args.repo or ".").resolve()
        node = args.node or args.node_arg
        if not node:
            raise ValueError("inspect requires a node. Example: csegraph inspect MyClass.method")
        result = GraphQueryService(_db_arg(args, str(repo))).neighborhood(node, depth=args.depth)
        result.command = "inspect"
        return result
    if args.command == "graph":
        from csegraph_core.graph.visual import VisualExportService
        repo = Path(args.repo or ".").resolve()
        db_path = _db_arg(args, str(repo))
        output = args.output or _default_graph_output_path(db_path)
        return VisualExportService(db_path).export(output)
    if args.command == "tree":
        from csegraph_core.graph.tree import TreeExportService
        repo = Path(args.repo or ".").resolve()
        db_path = _db_arg(args, str(repo))
        output = args.output or str(Path(db_path).resolve().with_name("csegraph-tree.html"))
        return TreeExportService(db_path).export(output)
    if args.command == "communities":
        from csegraph_core.graph.communities import detect_communities
        repo = _repo_arg(args)
        return detect_communities(_db_arg(args, repo))
    if args.command == "hooks":
        from csegraph_core.hooks import install_hooks, uninstall_hooks
        repo = _repo_arg(args)
        if args.hooks_command == "install":
            return install_hooks(repo)
        if args.hooks_command == "uninstall":
            return uninstall_hooks(repo)
        raise ValueError(f"Unknown hooks subcommand: {args.hooks_command}")
    if args.command == "report":
        from csegraph_core.graph.report import ReportService
        repo = _repo_arg(args)
        return ReportService(_db_arg(args, repo)).report()
    if args.command == "watch":
        from csegraph_core.watch import watch as run_watch
        repo = _repo_arg(args)
        run_watch(repo, _db_arg(args, repo), profile=args.profile, debounce_ms=args.debounce)
        return None
    if args.command == "serve":
        import asyncio
        from csegraph_core.server import run_stdio
        asyncio.run(run_stdio())
        return None
    if args.command == "status":
        from csegraph_core.status import StatusService
        repo = _repo_arg(args)
        return StatusService(_db_arg(args, repo)).status(verbose=args.verbose)
    if args.command == "postprocess":
        from csegraph_core.postprocess import PostprocessService
        repo = _repo_arg(args)
        return PostprocessService(_db_arg(args, repo)).postprocess(
            no_fts=args.no_fts,
            no_communities=args.no_communities,
        )
    if args.command == "benchmark":
        from csegraph_core.benchmark import BenchmarkService
        repo = _repo_arg(args)
        db_path = _db_arg(args, repo)
        return BenchmarkService(db_path).run(
            repo,
            profile=args.profile,
            query=args.query,
            target=args.target,
            graph_output_path=_default_graph_output_path(db_path),
        )
    raise ValueError(f"Unknown command: {args.command}")


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
    if args.db:
        return str(Path(args.db).resolve())
    return str(Path(repo).resolve() / ".csegraph" / "index.db")


def _default_graph_output_path(db_path: str) -> str:
    return str(Path(db_path).resolve().with_name("csegraph-graph.html"))
