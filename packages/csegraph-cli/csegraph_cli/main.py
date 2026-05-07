"""csegraph_cli.main — CLI entrypoint for csegraph.

Provides the `csegraph` shell command (registered via pyproject_cli.toml).
Importable standalone; depends on csegraph-core and lazy-loads optional
add-ons only when their subcommands need them.
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
from csegraph_core.index.services import IndexService, RefreshService
from csegraph_core.retrieval.context import ContextService


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        result = _dispatch(args)
    except Exception as exc:
        payload = {"error": str(exc)}
        print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)
        return 1

    payload = to_dict(result)
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
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
    context.add_argument("--profile", choices=sorted(PROFILES), default="medium")
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
    context.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    graph = subparsers.add_parser("graph", help="Explain a graph neighborhood.")
    graph.add_argument("node_arg", nargs="?", help="Node ID, symbol name, or file path.")
    graph.add_argument("--repo", default=None, help="Repository root containing the default .csegraph index.")
    graph.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    graph.add_argument("--node", default=None, help="Node ID, symbol name, or file path.")
    graph.add_argument("--depth", type=int, default=1, help="Neighborhood depth.")
    graph.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    codegen = subparsers.add_parser(
        "codegen",
        help="Generate code from graph-backed context + LLM.",
    )
    codegen.add_argument("task_arg", help="Natural-language task (required). Example: 'Add a calculator function'.")
    codegen.add_argument("--repo", default=None, help="Repository root containing the default .csegraph index.")
    codegen.add_argument("--db", default=None, help="SQLite database path (default: <repo>/.csegraph/index.db).")
    codegen.add_argument("--task", default=None, help="Natural-language task (alternative to positional).")
    codegen.add_argument("--target", default=None, help="Optional target node, symbol name, or file path.")
    codegen.add_argument("--profile", choices=sorted(PROFILES), default="medium")
    codegen.add_argument("-o", "--output", default=None, help="Write generated .py to this path.")
    codegen.add_argument("--model-path", default=None, help="Explicit GGUF model path (overrides auto-selection).")
    codegen.add_argument("--model-dir", default=None, help="Directory of GGUF models for auto-selection.")
    codegen.add_argument("--groq-model", default=None, help="Groq model ID for API fallback.")
    codegen.add_argument("--temperature", type=float, default=0.2, help="LLM sampling temperature (default: 0.2).")
    codegen.add_argument("--max-tokens", type=int, default=2048, help="Max tokens in completion (default: 2048).")
    codegen.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

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
        )
    if args.command == "graph":
        repo = Path(args.repo or ".").resolve()
        node = args.node or args.node_arg
        if not node:
            raise ValueError("graph requires a node. Example: csegraph graph MyClass.method")
        return GraphQueryService(_db_arg(args, str(repo))).neighborhood(node, depth=args.depth)
    if args.command == "codegen":
        try:
            from csegraph_codegen.service import CodegenService
        except ImportError as exc:
            raise RuntimeError(
                "codegen requires the optional csegraph-codegen package. "
                "Install with: pip install csegraph-codegen"
            ) from exc
        repo = Path(args.repo or ".").resolve()
        task = args.task or args.task_arg
        if not task:
            raise ValueError(
                'codegen requires a task. Example: csegraph codegen "Add a calculator function"'
            )
        kwargs: dict[str, Any] = {}
        if args.groq_model:
            kwargs["groq_model"] = args.groq_model
        if args.model_path:
            kwargs["model_path"] = args.model_path
        if args.model_dir:
            kwargs["model_dir"] = args.model_dir
        svc = CodegenService(
            _db_arg(args, str(repo)),
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            **kwargs,
        )
        return svc.generate(
            task=task,
            target=args.target,
            profile=args.profile,
            output_path=args.output,
        )
    raise ValueError(f"Unknown command: {args.command}")


def _repo_arg(args: argparse.Namespace) -> str:
    return str(Path(args.repo_opt or args.repo_arg or ".").resolve())


def _db_arg(args: argparse.Namespace, repo: str) -> str:
    if args.db:
        return str(Path(args.db).resolve())
    return str(Path(repo).resolve() / ".csegraph" / "index.db")
