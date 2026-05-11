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
from csegraph_core.graph.queries import GraphQueryService
from csegraph_core.graph.report import ReportService
from csegraph_core.graph.visual import VisualExportService
from csegraph_core.index.services import IndexService, RefreshService
from csegraph_core.retrieval.context import ContextService
from csegraph_cli.errors import CsegraphCLIError, error_payload
from csegraph_cli.renderer import (
    render_context_markdown,
    render_index_summary,
    render_json,
    render_refresh_summary,
    render_report_markdown,
    render_visual_export_summary,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        _validate_cli_options(args)
        _emit_deprecation_warnings(args)
        result = _dispatch(args)
    except Exception as exc:
        print(json.dumps(error_payload(exc), indent=2, sort_keys=True), file=sys.stderr)
        return 1

    payload = to_dict(result)
    if args.command == "context" and args.output_format == "markdown":
        print(render_context_markdown(payload), end="")
    elif args.command == "report" and not args.json:
        print(render_report_markdown(payload), end="")
    elif _is_visual_graph_export(args) and not args.json:
        print(render_visual_export_summary(payload), end="")
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
        description="SQLite-backed Python code graph indexing and context retrieval.",
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

    inspect = subparsers.add_parser("inspect", help="Inspect a graph neighborhood.")
    inspect.add_argument("node_arg", nargs="?", help="Node ID, symbol name, or file path.")
    inspect.add_argument("--repo", default=None, help="Repository root containing the default .csegraph index.")
    inspect.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    inspect.add_argument("--node", default=None, help="Node ID, symbol name, or file path.")
    inspect.add_argument("--depth", type=int, default=1, help="Neighborhood depth.")
    inspect.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    graph = subparsers.add_parser("graph", help="Export a visual HTML graph.")
    graph.add_argument("node_arg", nargs="?", help="(Deprecated) Node ID for neighborhood inspection.")
    graph.add_argument("--repo", default=None, help="Repository root containing the default .csegraph index.")
    graph.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    graph.add_argument("--node", default=None, help="(Deprecated) Node ID for neighborhood inspection.")
    graph.add_argument("--depth", type=int, default=1, help="Neighborhood depth (deprecated, use inspect).")
    graph.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output HTML file path (default: beside the SQLite index DB).",
    )
    graph.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    report = subparsers.add_parser("report", help="Generate a project report from the index.")
    report.add_argument("repo_arg", nargs="?", help="Repository root containing the default .csegraph index.")
    report.add_argument("--repo", dest="repo_opt", help="Repository root containing the default .csegraph index.")
    report.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    report.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    return parser


def _dispatch(args: argparse.Namespace) -> Any:
    if args.command == "index":
        repo = _repo_arg(args)
        return IndexService(_db_arg(args, repo)).index(repo, profile=args.profile)
    if args.command == "refresh":
        repo = _repo_arg(args)
        return RefreshService(_db_arg(args, repo)).refresh(profile=args.profile)
    if args.command == "context":
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
    if args.command == "inspect":
        repo = Path(args.repo or ".").resolve()
        node = args.node or args.node_arg
        if not node:
            raise ValueError("inspect requires a node. Example: csegraph inspect MyClass.method")
        result = GraphQueryService(_db_arg(args, str(repo))).neighborhood(node, depth=args.depth)
        result.command = "inspect"
        return result
    if args.command == "graph":
        repo = Path(args.repo or ".").resolve()
        node = args.node or args.node_arg
        db_path = _db_arg(args, str(repo))
        if node:
            return GraphQueryService(db_path).neighborhood(node, depth=args.depth)
        output = args.output or _default_graph_output_path(db_path)
        return VisualExportService(db_path).export(output)
    if args.command == "report":
        repo = _repo_arg(args)
        return ReportService(_db_arg(args, repo)).report()
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


def _emit_deprecation_warnings(args: argparse.Namespace) -> None:
    if args.command == "graph" and (args.node or args.node_arg):
        print(
            "Warning: csegraph graph <node> is deprecated; "
            "use csegraph inspect <node> instead.",
            file=sys.stderr,
        )


def _is_visual_graph_export(args: argparse.Namespace) -> bool:
    return args.command == "graph" and not (args.node or args.node_arg)


def _repo_arg(args: argparse.Namespace) -> str:
    return str(Path(args.repo_opt or args.repo_arg or ".").resolve())


def _db_arg(args: argparse.Namespace, repo: str) -> str:
    if args.db:
        return str(Path(args.db).resolve())
    return str(Path(repo).resolve() / ".csegraph" / "index.db")


def _default_graph_output_path(db_path: str) -> str:
    return str(Path(db_path).resolve().with_name("csegraph-graph.html"))
