"""Lean CseGraph 2.0 command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from csegraph._cli.errors import error_payload

PUBLIC_COMMANDS = (
    "index",
    "refresh",
    "context",
    "graph",
    "path",
    "status",
    "doctor",
    "install",
    "serve",
)

_PLATFORMS = (
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
)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _configure_logging(args)
    try:
        result = _dispatch(args)
    except Exception as exc:
        print(json.dumps(error_payload(exc), indent=2, sort_keys=True), file=sys.stderr)
        return 1
    if result is not None:
        _print_result(args, result)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="csegraph",
        description="Index code and retrieve compact, task-specific context.",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    subparsers = parser.add_subparsers(dest="command", required=True)

    index = subparsers.add_parser("index", help="Build a fresh project index.")
    _add_repo(index, positional=True)
    index.add_argument("--exclude", action="append", default=None, metavar="PATTERN")
    index.add_argument("--include-root", action="append", default=None, metavar="PATH")
    _add_json(index)

    refresh = subparsers.add_parser("refresh", help="Refresh changed files in an index.")
    _add_repo(refresh, positional=True)
    refresh.add_argument("--exclude", action="append", default=None, metavar="PATTERN")
    refresh.add_argument("--include-root", action="append", default=None, metavar="PATH")
    _add_json(refresh)

    context = subparsers.add_parser("context", help="Retrieve compact adaptive context.")
    context.add_argument("task", help="Natural-language coding task.")
    _add_repo(context)
    context.add_argument("--target")
    context.add_argument(
        "--task-kind",
        choices=("auto", "edit", "understand", "review", "test-impact"),
        default="auto",
    )
    context.add_argument("--token-budget", type=int, default=800)
    context.add_argument(
        "--source-mode",
        choices=("auto", "always", "never"),
        default="auto",
    )
    context.add_argument("--diagnostic", action="store_true")
    context.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "markdown"),
        default="json",
    )

    graph = subparsers.add_parser("graph", help="Inspect a focused graph neighborhood.")
    graph.add_argument("node", help="Node ID, symbol name, or file path.")
    _add_repo(graph)
    graph.add_argument("--depth", type=int, default=1)
    graph.add_argument("--relations")
    _add_json(graph)

    path = subparsers.add_parser("path", help="Find a focused dependency path.")
    path.add_argument("source", help="Source node ID, symbol name, or file path.")
    path.add_argument("target", help="Target node ID, symbol name, or file path.")
    _add_repo(path)
    path.add_argument("--relations")
    _add_json(path)

    status = subparsers.add_parser("status", help="Show index status and freshness.")
    _add_repo(status, positional=True)
    _add_json(status)

    doctor = subparsers.add_parser("doctor", help="Diagnose MCP client setup.")
    _add_repo(doctor, positional=True)
    doctor.add_argument("--platform", choices=_PLATFORMS, default="auto")
    doctor.add_argument("--command", dest="server_command", default="csegraph")
    doctor.add_argument("--no-verify", dest="verify", action="store_false", default=True)
    _add_json(doctor)

    install = subparsers.add_parser("install", help="Configure an MCP client.")
    _add_repo(install, positional=True)
    install.add_argument("--platform", choices=_PLATFORMS, default="auto")
    install.add_argument("--command", dest="server_command", default="csegraph")
    install.add_argument("--dry-run", action="store_true")
    install.add_argument("--no-verify", dest="verify", action="store_false", default=True)
    _add_json(install)

    serve = subparsers.add_parser("serve", help="Start the MCP stdio server.")
    _add_repo(serve)
    serve.add_argument(
        "--tools",
        help="Use 'core' or a comma-separated subset of the six MCP tools.",
    )
    serve.add_argument("--platform", choices=_PLATFORMS[1:])

    return parser


def _add_repo(parser: argparse.ArgumentParser, *, positional: bool = False) -> None:
    if positional:
        parser.add_argument("repo_arg", nargs="?", help="Repository root.")
    parser.add_argument("--repo", dest="repo_opt", help="Repository root.")


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit JSON.")


def _configure_logging(args: argparse.Namespace) -> None:
    level = logging.DEBUG if args.verbose > 1 else logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(level=level, stream=sys.stderr, force=True)


def _dispatch(args: argparse.Namespace) -> Any:
    repo = _repo(args)
    db_path = str(Path(repo) / ".csegraph" / "index.db")

    if args.command == "index":
        from csegraph._core.index.services import IndexService

        return IndexService(db_path).index(
            repo,
            exclude_patterns=args.exclude,
            include_roots=args.include_root,
        )
    if args.command == "refresh":
        from csegraph._core.index.services import RefreshService

        return RefreshService(db_path).refresh(
            exclude_patterns=args.exclude,
            include_roots=args.include_root,
        )
    if args.command == "context":
        from csegraph._core.core.models import ContextRequest
        from csegraph._core.retrieval.context import ContextService

        return ContextService(db_path).retrieve(
            ContextRequest(
                task=args.task,
                repo=repo,
                target=args.target,
                task_kind=args.task_kind,
                token_budget=args.token_budget,
                source_mode=args.source_mode,
                diagnostic=args.diagnostic,
            )
        )
    if args.command == "graph":
        from csegraph._core.graph.queries import GraphQueryService

        return GraphQueryService(db_path).neighborhood(
            args.node,
            depth=args.depth,
            relations=_relations(args.relations),
        )
    if args.command == "path":
        from csegraph._core.graph.queries import GraphQueryService

        return GraphQueryService(db_path).shortest_path(
            args.source,
            args.target,
            relations=_relations(args.relations),
        )
    if args.command == "status":
        from csegraph._core.status import StatusService

        return StatusService(db_path).status()
    if args.command == "doctor":
        from csegraph._core.mcp_doctor import McpDoctorService

        service = McpDoctorService(repo, command=args.server_command)
        if args.platform == "auto":
            return service.doctor_all(verify=args.verify)
        return service.doctor(platform=args.platform, verify=args.verify)
    if args.command == "install":
        from csegraph._core.mcp_install import McpInstallService

        return McpInstallService(repo, command=args.server_command).install(
            platform=args.platform,
            dry_run=args.dry_run,
            instructions=None,
            hooks=None,
            gitignore=None,
            verify=args.verify,
        )
    if args.command == "serve":
        from csegraph._core.server import run_stdio

        allowed = _allowed_tools(args.tools)
        asyncio.run(
            run_stdio(
                allowed_tools=allowed,
                bound_repo=repo if args.repo_opt else None,
                host_platform=args.platform,
            )
        )
        return None
    raise ValueError(f"Unknown command: {args.command}")


def _print_result(args: argparse.Namespace, result: Any) -> None:
    from csegraph._cli.renderer import (
        render_context_markdown,
        render_index_summary,
        render_install_summary,
        render_json,
        render_refresh_summary,
        render_status_summary,
    )
    from csegraph._core.core.serializer import to_dict

    payload = to_dict(result)
    if args.command == "context" and args.output_format == "markdown":
        print(render_context_markdown(payload), end="")
    elif args.command == "index" and not args.json:
        print(render_index_summary(payload), end="")
    elif args.command == "refresh" and not args.json:
        print(render_refresh_summary(payload), end="")
    elif args.command == "status" and not args.json:
        print(render_status_summary(payload), end="")
    elif args.command in {"doctor", "install"} and not args.json:
        print(render_install_summary(payload), end="")
    else:
        print(render_json(payload, compact=True))


def _repo(args: argparse.Namespace) -> str:
    return str(
        Path(getattr(args, "repo_opt", None) or getattr(args, "repo_arg", None) or ".").resolve()
    )


def _relations(raw: str | None) -> list[str] | None:
    return [item.strip() for item in raw.split(",") if item.strip()] if raw else None


def _allowed_tools(raw: str | None) -> list[str] | None:
    if raw is None or raw == "core":
        return None
    tools = [item.strip() for item in raw.split(",") if item.strip()]
    if not tools:
        raise ValueError("--tools must be 'core' or a comma-separated tool list")
    return tools


if __name__ == "__main__":
    raise SystemExit(main())
