"""Compare adaptive retrieval with a versioned rg/Pyright selective-read baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import csegraph
from csegraph import ContextRequest, ContextService, IndexService, to_dict
from csegraph._core.retrieval.token_budget import (
    response_tokens,
    token_measurement,
)
from tools.benchmarks.baseline import (
    PINNED_PYRIGHT_VERSION,
    PyrightLspProvider,
    StrongBaselineAdapter,
)
from tools.benchmarks.corpora import build_adaptive_corpus
from tools.benchmarks.execution import execute_benchmark_task
from tools.benchmarks.models import (
    AdaptiveBenchmarkCorpus,
    AdaptiveBenchmarkTask,
    BenchmarkRepository,
)
from tools.benchmarks.quality import corpus_completeness, corpus_quality
from tools.benchmarks.reporting import (
    _aggregate_system,
    _content_tokens_without_diagnostics,
    _context_task_kind,
    _diagnostic_measurement_budget,
    _evaluate_v2_result,
    _precision,
    _recall,
    _summary,
    _unmeasured_task,
)
from tools.benchmarks.schema import corpus_to_payload
from tools.benchmarks.schema import load_corpus as load_adaptive_corpus
from tools.benchmarks.workspace import (
    LOCAL_COPY_URLS,
    benchmark_workspace_hygiene,
    copy_benchmark_repository,
    prepare_benchmark_repository,
)

REPORT_SCHEMA_VERSION = "csegraph-adaptive-retrieval-report-v4"
RUNNER_VERSION = "2.3"
DIAGNOSTIC_BUDGET_SLACK = 1024


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--corpus",
        default="pr",
        help="Named source-driven corpus (pr, nightly, release) or a JSON path",
    )
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--bootstrap-missing", action="store_true")
    parser.add_argument("--budget", type=int, default=800)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--modes",
        default="cold,warm",
        help="Comma-separated measurements: cold,warm (warm includes an unreported warmup)",
    )
    parser.add_argument("--warm-runs", type=int, default=1)
    parser.add_argument(
        "--samples",
        type=int,
        default=None,
        help="Alias for --warm-runs; use with warm mode for repeated measurement samples",
    )
    parser.add_argument(
        "--pyright",
        choices=("auto", "off", "required"),
        default="auto",
        help=f"Use pinned Pyright {PINNED_PYRIGHT_VERSION} when available",
    )
    parser.add_argument("--execute-tasks", action="store_true")
    parser.add_argument(
        "--agent-command",
        default=None,
        help="Agent argv with optional {task}, {target}, {repo}, and {task_id} placeholders",
    )
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--output", default=None)
    parser.add_argument("--fail-on-gates", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    corpus_path: Path | None = None
    if args.corpus in {"pr", "nightly", "release"}:
        corpus = build_adaptive_corpus(args.corpus, repo_root=repo_root)
        corpus_digest = hashlib.sha256(
            json.dumps(
                corpus_to_payload(corpus),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    else:
        corpus_path = Path(args.corpus).resolve()
        corpus = load_adaptive_corpus(corpus_path)
        corpus_digest = _sha256(corpus_path)
    tasks = list(corpus.tasks)
    if args.limit is not None:
        tasks = tasks[: max(0, args.limit)]
    modes = _parse_modes(args.modes)
    if args.samples is not None:
        args.warm_runs = args.samples
    if args.warm_runs < 1:
        parser.error("--warm-runs/--samples must be at least 1")

    cache_root = (
        Path(args.cache_dir).resolve()
        if args.cache_dir
        else Path(tempfile.gettempdir()) / "csegraph-benchmark-repositories"
    )
    provider = None if args.pyright == "off" else PyrightLspProvider()
    baseline = StrongBaselineAdapter(definition_provider=provider)
    started_at = datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []
    repository_results: dict[str, dict[str, Any]] = {}

    with tempfile.TemporaryDirectory(prefix="csegraph-adaptive-benchmark-") as tmp:
        scratch = Path(tmp)
        indexed: dict[str, tuple[Path, Path, float]] = {}
        for task in tasks:
            repository = corpus.repositories.get(task.repo)
            if repository is None:
                results.append(_unmeasured_task(task, "repository_not_declared"))
                continue
            if task.repo not in indexed and task.repo not in repository_results:
                prepared = prepare_benchmark_repository(
                    repository,
                    repo_root=repo_root,
                    cache_root=cache_root,
                    bootstrap_missing=args.bootstrap_missing,
                )
                repository_results[task.repo] = {
                    "requested_path": str((repo_root / task.repo).resolve()),
                    "resolved_path": str(prepared.path) if prepared.path else None,
                    "url": repository.url,
                    "expected_commit": repository.commit,
                    "observed_commit": prepared.observed_commit,
                    "commit_matches": prepared.commit_matches,
                    "bootstrapped": prepared.bootstrapped,
                    "reason": prepared.reason,
                }
                if prepared.path is not None and prepared.commit_matches:
                    benchmark_repo = prepared.path
                    if _copy_repository_for_benchmark(repository):
                        benchmark_repo = scratch / f"fixture-{len(indexed)}"
                        hygiene = copy_benchmark_repository(prepared.path, benchmark_repo)
                    else:
                        hygiene = benchmark_workspace_hygiene(benchmark_repo)
                    db = scratch / f"repo-{len(indexed)}.db"
                    index_started = time.perf_counter()
                    index_result = IndexService(db).index(benchmark_repo)
                    index_ms = (time.perf_counter() - index_started) * 1000
                    repository_results[task.repo]["workspace_hygiene"] = hygiene
                    repository_results[task.repo]["index"] = _index_report(
                        index_result,
                        db,
                        index_ms,
                    )
                    indexed[task.repo] = (benchmark_repo, db, index_ms)

            prepared_index = indexed.get(task.repo)
            if prepared_index is None:
                reason = str(repository_results[task.repo].get("reason") or "not_indexed")
                results.append(_unmeasured_task(task, reason))
                continue
            repo, db, index_ms = prepared_index
            results.append(
                _run_retrieval_task(
                    task,
                    repo,
                    db,
                    baseline,
                    budget=args.budget,
                    modes=modes,
                    warm_runs=args.warm_runs,
                    index_ms=index_ms,
                    execute_tasks=args.execute_tasks,
                    agent_command=(
                        tuple(shlex.split(args.agent_command)) if args.agent_command else None
                    ),
                    allow_network=args.allow_network,
                )
            )

    if provider is not None:
        provider.close()
    completeness = corpus_completeness(corpus)
    quality = corpus_quality(corpus)
    # A limited developer run is intentionally not a complete release-gate run.
    evaluated_complete_corpus = args.limit is None
    summary = _summary(
        results,
        args.budget,
        completeness=completeness,
        quality=quality,
        evaluated_complete_corpus=evaluated_complete_corpus,
        pyright_required=args.pyright == "required",
        pyright_available=bool(provider and provider.available),
    )
    finished_at = datetime.now(timezone.utc)
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
        "generated_at": finished_at.isoformat(),
        "duration_ms": round((finished_at - started_at).total_seconds() * 1000, 3),
        "corpus": {
            "path": str(corpus.path),
            "schema_version": corpus.schema_version,
            "version": corpus.version,
            "tier": corpus.tier,
            "status": corpus.status,
            "unsupported_reason": corpus.unsupported_reason,
            "sha256": corpus_digest,
            "completeness": completeness,
            "quality": quality,
            "limited_to": args.limit,
        },
        "configuration": {
            "token_budget": args.budget,
            "modes": list(modes),
            "warm_runs": args.warm_runs,
            "samples": args.warm_runs,
            "bootstrap_missing": args.bootstrap_missing,
            "pyright_mode": args.pyright,
            "execute_tasks": args.execute_tasks,
            "allow_network": args.allow_network,
        },
        "provenance": _provenance(
            repo_root,
            corpus,
            provider,
        ),
        "repositories": repository_results,
        "summary": summary,
        "tasks": results,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    if args.fail_on_gates and not summary["release_gates_passed"]:
        return 1
    return 0


def _run_retrieval_task(
    task: AdaptiveBenchmarkTask,
    repo: Path,
    db: Path,
    baseline: StrongBaselineAdapter,
    *,
    budget: int,
    modes: Sequence[str],
    warm_runs: int,
    index_ms: float,
    execute_tasks: bool,
    agent_command: Sequence[str] | None,
    allow_network: bool,
) -> dict[str, Any]:
    service = ContextService(db)
    measurements: dict[str, Any] = {}
    if "warm" in modes and "cold" not in modes:
        _measure_pair(task, repo, service, baseline, budget=budget, temperature="warmup")
    for mode in modes:
        repetitions = warm_runs if mode == "warm" else 1
        samples = [
            _measure_pair(task, repo, service, baseline, budget=budget, temperature=mode)
            for _ in range(repetitions)
        ]
        measurements[mode] = {
            "samples": samples,
            "baseline": _aggregate_system(samples, "baseline"),
            "adaptive": _aggregate_system(samples, "adaptive"),
        }
    selected_mode = "warm" if "warm" in measurements else modes[-1]
    baseline_result = measurements[selected_mode]["baseline"]
    adaptive_result = measurements[selected_mode]["adaptive"]
    expected = set(task.expected_locations)
    permitted = set(task.permitted_files)
    uses_v2_expectations = bool(
        task.expected_target
        or task.expected_candidates
        or task.required_evidence
        or task.permitted_ranges
        or task.expected_next_tool
        or task.expected_status != "ready"
    )
    if uses_v2_expectations:
        baseline_evaluation = _evaluate_v2_result(task, baseline_result, adaptive=False)
        adaptive_evaluation = _evaluate_v2_result(task, adaptive_result, adaptive=True)
    else:
        baseline_evaluation = {
            "recall": _recall(set(baseline_result["paths"]), expected),
            "target_found": (
                not task.expected_locations
                or task.expected_locations[0] in set(baseline_result["paths"])
            ),
            "precision": _precision(set(baseline_result["paths"]), permitted),
            "status_matched": baseline_result["status"] == "ready",
            "role_recall": 1.0,
            "next_tool_matched": True,
        }
        adaptive_evaluation = {
            "recall": _recall(set(adaptive_result["paths"]), expected),
            "target_found": adaptive_result["status"] == "ready",
            "precision": _precision(set(adaptive_result["paths"]), permitted),
            "status_matched": adaptive_result["status"] == "ready",
            "role_recall": 1.0,
            "next_tool_matched": True,
        }
    execution = None
    if execute_tasks:
        execution = asdict(
            execute_benchmark_task(
                task,
                repo,
                agent_command=agent_command,
                allow_network=allow_network,
            )
        )
    return {
        "id": task.id,
        "status": "measured",
        "category": task.category,
        "execution_mode": task.execution_mode,
        "repo": task.repo,
        "commit": task.commit,
        "index_ms": round(index_ms, 3),
        "expected_locations": sorted(expected),
        "permitted_files": sorted(permitted),
        "expected_status": task.expected_status,
        **(
            {"expected_target": asdict(task.expected_target)}
            if task.expected_target is not None
            else {}
        ),
        **(
            {"expected_candidates": [asdict(item) for item in task.expected_candidates]}
            if task.expected_candidates
            else {}
        ),
        **(
            {"required_evidence": [asdict(item) for item in task.required_evidence]}
            if task.required_evidence
            else {}
        ),
        **(
            {"permitted_ranges": [asdict(item) for item in task.permitted_ranges]}
            if task.permitted_ranges
            else {}
        ),
        "selected_mode": selected_mode,
        "measurements": measurements,
        "baseline": {
            **baseline_result,
            **baseline_evaluation,
        },
        "adaptive": {
            **adaptive_result,
            **adaptive_evaluation,
        },
        **({"execution": execution} if execution is not None else {}),
    }


def _measure_pair(
    task: AdaptiveBenchmarkTask,
    repo: Path,
    service: ContextService,
    baseline: StrongBaselineAdapter,
    *,
    budget: int,
    temperature: str,
) -> dict[str, Any]:
    baseline_started = time.perf_counter()
    baseline_result = baseline.retrieve(
        repo,
        task.task,
        target=task.target,
        task_kind=task.category,
        token_budget=budget,
        temperature=temperature,
    )
    baseline_observed_ms = (time.perf_counter() - baseline_started) * 1000

    adaptive_started = time.perf_counter()
    adaptive = service.retrieve(
        ContextRequest(
            repo=str(repo),
            task=task.task,
            target=task.target,
            task_kind=_context_task_kind(task.category),
            token_budget=_diagnostic_measurement_budget(task, budget),
            diagnostic=True,
        )
    )
    adaptive_observed_ms = (time.perf_counter() - adaptive_started) * 1000
    adaptive_payload = to_dict(adaptive)
    adaptive_diagnostics = adaptive_payload.get("diagnostics") or {}
    adaptive_usage = adaptive_diagnostics.get("usage") or {}
    adaptive_engine_ms = float(adaptive_usage.get("latency_ms") or adaptive_observed_ms)
    adaptive_diagnostic_tokens = int(adaptive_usage.get("tokens") or response_tokens(adaptive))
    adaptive_tokens = _content_tokens_without_diagnostics(adaptive_payload)
    target_slice = next((item for item in adaptive.slices if item.role == "target"), None)
    adaptive_target = (
        {
            "id": target_slice.id,
            "name": target_slice.symbol,
            "path": target_slice.path,
            "lines": target_slice.lines,
        }
        if target_slice is not None
        else None
    )
    return {
        "baseline": {
            "status": "ready" if baseline_result.slices else "empty",
            "tokens": baseline_result.usage["tokens"],
            "measurement": baseline_result.usage.get("measurement"),
            "tool_observed_latency_ms": round(baseline_observed_ms, 3),
            "tool_latency_ms": baseline_result.usage["tool_latency_ms"],
            "engine_latency_ms": baseline_result.usage["engine_latency_ms"],
            "external_tool_latency_ms": baseline_result.usage["external_tool_latency_ms"],
            "rg_latency_ms": baseline_result.usage["rg_latency_ms"],
            "lsp_latency_ms": baseline_result.usage["lsp_latency_ms"],
            "tool_calls": baseline_result.usage["tool_calls"],
            "paths": sorted({item.path for item in baseline_result.slices}),
            "slices": [asdict(item) for item in baseline_result.slices],
            "warnings": baseline_result.warnings,
        },
        "adaptive": {
            "status": adaptive_payload["status"],
            "tokens": adaptive_tokens,
            "diagnostic_tokens": adaptive_diagnostic_tokens,
            "diagnostic_budget": _diagnostic_measurement_budget(task, budget),
            "measurement": adaptive_usage.get("measurement") or token_measurement("o200k_base"),
            "tool_observed_latency_ms": round(adaptive_observed_ms, 3),
            "tool_latency_ms": round(adaptive_observed_ms, 3),
            "engine_latency_ms": round(adaptive_engine_ms, 3),
            "external_tool_latency_ms": round(
                max(0.0, adaptive_observed_ms - adaptive_engine_ms), 3
            ),
            "tool_calls": 1,
            "cache": adaptive_usage.get("cache", "disabled"),
            "paths": sorted({item.path for item in adaptive.slices}),
            "target": adaptive_target,
            "candidates": adaptive_payload.get("candidates", []),
            "slices": adaptive_payload.get("slices", []),
            "next": adaptive_payload.get("next"),
        },
    }


def _copy_repository_for_benchmark(repository: BenchmarkRepository) -> bool:
    return repository.url in LOCAL_COPY_URLS


def _index_report(index_result: Any, db_path: Path, index_ms: float) -> dict[str, Any]:
    timings = dict(getattr(index_result, "timings_ms", {}) or {})
    parse_cache_path = db_path.with_name("parse_cache.db")
    return {
        "files_indexed": int(getattr(index_result, "files_indexed", 0)),
        "symbols_indexed": int(getattr(index_result, "symbols_indexed", 0)),
        "edges_indexed": int(getattr(index_result, "edges_indexed", 0)),
        "cache_hits": int(getattr(index_result, "cache_hits", 0)),
        "cache_misses": int(getattr(index_result, "cache_misses", 0)),
        "index_ms": round(index_ms, 3),
        "discover_parse_ms": round(float(timings.get("discover_parse") or 0.0), 3),
        "write_graph_ms": round(float(timings.get("write_graph") or 0.0), 3),
        "db_size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "parse_cache_size_bytes": (
            parse_cache_path.stat().st_size if parse_cache_path.exists() else 0
        ),
        "timings_ms": timings,
    }


def _parse_modes(raw: str) -> tuple[str, ...]:
    modes = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    if not modes or any(mode not in {"cold", "warm"} for mode in modes):
        raise SystemExit("--modes must contain cold, warm, or cold,warm")
    return modes


def _provenance(
    repo_root: Path,
    corpus: AdaptiveBenchmarkCorpus,
    provider: PyrightLspProvider | None,
) -> dict[str, Any]:
    return {
        "csegraph_version": csegraph.__version__,
        "runner_git_commit": _command_output(["git", "-C", str(repo_root), "rev-parse", "HEAD"]),
        "runner_git_dirty": bool(
            _command_output(["git", "-C", str(repo_root), "status", "--porcelain"])
        ),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "rg_version": _first_line(_command_output(["rg", "--version"])),
        "git_version": _first_line(_command_output(["git", "--version"])),
        "pyright": {
            "enabled": provider is not None,
            "available": bool(provider and provider.available),
            "expected_version": PINNED_PYRIGHT_VERSION,
            "observed_version": provider.observed_version if provider else None,
            "warning": provider.warning if provider else "disabled by configuration",
        },
        "corpus_version": corpus.version,
        "command": sys.argv,
    }


def _command_output(argv: Sequence[str]) -> str | None:
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _first_line(value: str | None) -> str | None:
    return value.splitlines()[0] if value else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
