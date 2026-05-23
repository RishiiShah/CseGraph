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
    render_install_summary,
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
    elif args.command == "install" and not args.json:
        print(render_install_summary(payload), end="")
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


def _add_repo_positional(p: argparse.ArgumentParser) -> None:
    p.add_argument("repo_arg", nargs="?", help="Repository root (default: current directory).")
    p.add_argument("--repo", dest="repo_opt", help="Repository root.")


def _add_db(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")


def _add_json(p: argparse.ArgumentParser, *, suppress: bool = False) -> None:
    p.add_argument("--json", action="store_true", help=argparse.SUPPRESS if suppress else "Emit machine-readable JSON.")


def _add_profile(p: argparse.ArgumentParser, *, default: str = "medium") -> None:
    p.add_argument("--profile", choices=sorted(PROFILES), default=default)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="csegraph",
        description="SQLite-backed code graph indexing and context retrieval.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index = subparsers.add_parser("index", help="Build a fresh project index.")
    _add_repo_positional(index)
    _add_db(index)
    _add_profile(index)
    _add_json(index)

    refresh = subparsers.add_parser("refresh", help="Refresh changed files in an index.")
    _add_repo_positional(refresh)
    _add_db(refresh)
    _add_profile(refresh)
    _add_json(refresh)

    minimal = subparsers.add_parser(
        "minimal",
        help="Compact routing card with key entities and next-tool suggestions (call first).",
    )
    minimal.add_argument(
        "--repo",
        default=None,
        help="Repository root containing the default .csegraph index.",
    )
    minimal.add_argument(
        "--task",
        default=None,
        help="Optional task description for keyword routing.",
    )
    _add_db(minimal)
    _add_json(minimal)

    context = subparsers.add_parser("context", help="Retrieve graph-backed context.")
    context.add_argument("task_arg", nargs="?", help="Natural-language task.")
    context.add_argument("--repo", default=None, help="Repository root containing the default .csegraph index.")
    _add_db(context)
    context.add_argument("--task", default=None, help="Natural-language task.")
    context.add_argument("--target", default=None, help="Optional target node, symbol name, or file path.")
    _add_profile(context, default=None)
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
    path.add_argument("--repo", default=None, help="Repository root containing the default .csegraph index.")
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
    inspect.add_argument("--repo", default=None, help="Repository root containing the default .csegraph index.")
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

    graph = subparsers.add_parser("graph", help="Export a visual HTML graph.")
    graph.add_argument("--repo", default=None, help="Repository root containing the default .csegraph index.")
    _add_db(graph)
    graph.add_argument("--output", "-o", default=None, help="Output HTML file path (default: beside the SQLite index DB).")
    _add_json(graph)

    tree = subparsers.add_parser("tree", help="Export an interactive HTML file tree visualization.")
    tree.add_argument("--repo", default=None, help="Repository root containing the default .csegraph index.")
    _add_db(tree)
    tree.add_argument("--output", "-o", default=None, help="Output HTML file path (default: beside the SQLite index DB).")
    _add_json(tree)

    communities = subparsers.add_parser("communities", help="Detect communities in the dependency graph.")
    _add_repo_positional(communities)
    _add_db(communities)
    _add_json(communities)

    hooks = subparsers.add_parser("hooks", help="Manage csegraph git hooks.")
    hooks_sub = hooks.add_subparsers(dest="hooks_command", required=True)
    for name, help_text in [("install", "Install post-commit/merge/checkout hooks."), ("uninstall", "Remove csegraph git hooks.")]:
        sub = hooks_sub.add_parser(name, help=help_text)
        _add_repo_positional(sub)
        _add_json(sub)

    install = subparsers.add_parser("install", help="Configure MCP clients to run csegraph serve.")
    _add_repo_positional(install)
    install.add_argument(
        "--platform",
        choices=["auto", "codex", "cursor", "claude-code", "gemini-cli", "kiro", "copilot"],
        default="auto",
        help="MCP client platform to configure.",
    )
    install.add_argument(
        "--command",
        dest="server_command",
        default="csegraph",
        help="Executable command used by MCP clients to launch csegraph.",
    )
    install.add_argument("--dry-run", action="store_true", help="Show planned writes without modifying files.")
    install.add_argument("--instructions", action="store_true", help="Generate platform instruction files (CLAUDE.md, AGENTS.md, GEMINI.md, CODEX.md).")
    install.add_argument("--hooks", action="store_true", help="Install agent hooks for auto-refresh and status checks.")
    _add_json(install)

    report = subparsers.add_parser("report", help="Generate a project report from the index.")
    _add_repo_positional(report)
    _add_db(report)
    _add_json(report)

    watch = subparsers.add_parser("watch", help="Watch for file changes and auto-refresh the index.")
    _add_repo_positional(watch)
    _add_db(watch)
    _add_profile(watch)
    watch.add_argument("--debounce", type=int, default=500, help="Debounce interval in milliseconds (default: 500).")
    _add_json(watch, suppress=True)

    serve = subparsers.add_parser("serve", help="Start the MCP stdio server for coding agents.")
    serve.add_argument(
        "--tools",
        default=None,
        help="Comma-separated list of tool names to expose (e.g. 'csegraph_minimal,csegraph_context'). Default: all tools.",
    )
    _add_json(serve, suppress=True)

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
    _add_repo_positional(benchmark)
    _add_db(benchmark)
    _add_profile(benchmark)
    benchmark.add_argument("--query", default="Benchmark context retrieval", help="Context query to benchmark.")
    benchmark.add_argument("--target", default=None, help="Optional context target symbol.")
    benchmark.add_argument(
        "--expect-node",
        action="append",
        default=[],
        help="Expected context node ID for benchmark quality checks. May be repeated.",
    )
    _add_json(benchmark)

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
    if args.command == "minimal":
        from csegraph_core.retrieval.minimal import MinimalService
        repo = Path(args.repo or ".").resolve()
        return MinimalService(_db_arg(args, str(repo))).first(task=args.task)
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
            detail_level=args.detail_level,
        )
    if args.command == "path":
        from csegraph_core.graph.queries import GraphQueryService
        repo = Path(args.repo or ".").resolve()
        source = args.source or args.source_arg
        target = args.target or args.target_arg
        if not source or not target:
            raise ValueError("path requires two nodes. Example: csegraph path greet main")
        relations = [r.strip() for r in args.relations.split(',') if r.strip()] if args.relations else None
        return GraphQueryService(_db_arg(args, str(repo))).shortest_path(
            source, target, detail_level=args.detail_level, relations=relations
        )
    if args.command == "inspect":
        from csegraph_core.graph.queries import GraphQueryService
        repo = Path(args.repo or ".").resolve()
        node = args.node or args.node_arg
        if not node:
            raise ValueError("inspect requires a node. Example: csegraph inspect MyClass.method")
        relations = (
            [r.strip() for r in args.relations.split(",") if r.strip()]
            if args.relations
            else None
        )
        result = GraphQueryService(_db_arg(args, str(repo))).neighborhood(
            node,
            depth=args.depth,
            detail_level=args.detail_level,
            relations=relations,
        )
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
    if args.command == "install":
        from csegraph_core.mcp_install import McpInstallService
        repo = _repo_arg(args)
        return McpInstallService(repo, command=args.server_command).install(
            platform=args.platform,
            dry_run=args.dry_run,
            instructions=args.instructions,
            hooks=args.hooks,
        )
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
        allowed = [t.strip() for t in args.tools.split(",") if t.strip()] if args.tools else None
        asyncio.run(run_stdio(allowed_tools=allowed))
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
            expected_nodes=args.expect_node,
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


def _assert_safe_path(path: Path, repo_path: Path, name: str) -> None:
    import tempfile
    resolved_path = path.resolve()
    resolved_repo = repo_path.resolve()
    if resolved_path.is_relative_to(resolved_repo):
        return
    temp_dir = Path(tempfile.gettempdir()).resolve()
    if resolved_path.is_relative_to(temp_dir):
        return
    try:
        home_dir = Path.home().resolve()
        if resolved_path.is_relative_to(home_dir):
            return
    except Exception:
        pass
    try:
        cwd_dir = Path.cwd().resolve()
        if resolved_path.is_relative_to(cwd_dir):
            return
    except Exception:
        pass
    raise ValueError(f"{name} path '{path}' must be within repository root, home directory, temporary directory, or CWD.")


def _db_arg(args: argparse.Namespace, repo: str) -> str:
    repo_path = Path(repo).resolve()
    if args.db:
        db_path = Path(args.db).resolve()
        _assert_safe_path(db_path, repo_path, "Database")
        return str(db_path)
    return str(repo_path / ".csegraph" / "index.db")


def _default_graph_output_path(db_path: str) -> str:
    return str(Path(db_path).resolve().with_name("csegraph-graph.html"))
