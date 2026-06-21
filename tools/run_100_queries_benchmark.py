"""Native MCP 100-query benchmark.

This benchmark uses the same stdio JSON-RPC path used by coding agents. It
does not import CseGraph engine internals; indexing and retrieval happen by
spawning the MCP server and calling tools through ``mcp.client``.

By default the workload is a cloned sandbox repository, not CseGraph itself.
Set ``CSEGRAPH_100_REPO`` to another repository path when needed.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from cross_repo_benchmark import (
    _OPENAI_TOKENIZER,
    _OPENAI_TOKENIZER_LABEL,
    REPO_ROOT,
    SANDBOX_DIR,
    NativeMcpClient,
    add_optional,
    collect_repo_snapshot,
    env_int,
    format_bytes,
    format_optional_int,
    format_optional_ratio,
    generate_queries,
    git_value,
    mean,
    multiply_optional,
    percentile,
    ratio_optional,
    run_context_query,
    server_command_from_env,
)


def workload_repo_from_env() -> Path:
    raw = os.environ.get("CSEGRAPH_100_REPO")
    if raw:
        repo = Path(raw).expanduser().resolve()
        if repo.exists():
            return repo
        raise FileNotFoundError(f"CSEGRAPH_100_REPO does not exist: {repo}")
    sandbox_names = [name.strip() for name in os.environ.get("CSEGRAPH_BENCH_REPOS", "").split(",")]
    for name in sandbox_names:
        candidate = SANDBOX_DIR / name
        if name and candidate.exists():
            return candidate
    for name in ("flask", "fastapi", "pytest", "micrograd"):
        candidate = SANDBOX_DIR / name
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "No sandbox workload repository found. Populate sandbox/ or set CSEGRAPH_100_REPO."
    )


async def main_async() -> None:
    query_limit = env_int("CSEGRAPH_100_QUERY_LIMIT", env_int("CSEGRAPH_BENCH_QUERY_LIMIT", 100))
    iterations = env_int("CSEGRAPH_100_ITERATIONS", 1)
    report_path = Path(
        os.environ.get(
            "CSEGRAPH_100_QUERIES_REPORT",
            str(REPO_ROOT / "benchmark_results" / "native_mcp_100_queries.md"),
        )
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)

    profile = os.environ.get("CSEGRAPH_BENCH_PROFILE", "auto")
    postprocess_level = os.environ.get("CSEGRAPH_BENCH_POSTPROCESS_LEVEL", "minimal")
    detail_level = os.environ.get("CSEGRAPH_BENCH_DETAIL_LEVEL", "standard")
    command, args = server_command_from_env()
    workload_repo = workload_repo_from_env()
    db_path = workload_repo / ".csegraph" / "index.db"

    print(f"Scanning {workload_repo} for naive full-read baseline...")
    snapshot = collect_repo_snapshot(workload_repo)
    queries = generate_queries(snapshot, limit=query_limit)
    print(
        f"Repository stats: {snapshot.files:,} source/text files, "
        f"{format_bytes(snapshot.bytes)}, {snapshot.lines:,} lines."
    )
    print(f"Generated {len(queries)} tailored queries.")

    mcp_latencies: list[float] = []
    mcp_content_bytes = 0
    mcp_content_chars4_tokens = 0
    mcp_content_openai_o200k_tokens: int | None = 0 if _OPENAI_TOKENIZER is not None else None
    mcp_envelope_chars4_tokens = 0
    mcp_envelope_openai_o200k_tokens: int | None = 0 if _OPENAI_TOKENIZER is not None else None
    mcp_tokenization_ms = 0.0
    errors: list[str] = []

    with report_path.open("w", encoding="utf-8") as report:
        report.write("# Native MCP 100-Query Benchmark Results\n\n")
        report.write(
            "This benchmark launches the CseGraph MCP server as a separate stdio "
            "process and calls tools through the official `mcp.client` JSON-RPC path. "
            "It does not import CseGraph SDK internals for indexing or retrieval. "
            "The default workload is a repository under `sandbox/`, not CseGraph itself.\n\n"
        )
        report.write("## Methodology\n")
        report.write(
            "- MCP latency is measured as the client-side round trip around "
            "`session.call_tool(...)`; token counting and report writing are excluded.\n"
        )
        report.write(
            "- The naive baseline is a full read of all included source/text files, "
            "multiplied once per completed MCP context call.\n"
        )
        report.write("- Exact UTF-8 byte counts are the canonical provider-neutral size metric.\n")
        report.write(
            "- `chars/4` counts are CseGraph's simple transparent heuristic and are "
            "reported separately from tokenizer-specific counts.\n"
        )
        report.write(
            "- OpenAI proxy counts use local `tiktoken` with `o200k_base` by default. "
            "Set `CSEGRAPH_BENCH_TOKENIZER_MODEL` or `CSEGRAPH_BENCH_OPENAI_ENCODING` "
            "to make that proxy explicit for a different OpenAI-family model.\n"
        )
        report.write(
            "- Claude and Gemini exact token counts require separate provider token-count "
            "APIs. Composer/Cursor counts are not labeled exact unless backed by a "
            "provider-native tokenizer/count API.\n\n"
        )
        report.write("## Run Metadata\n")
        report.write(f"- **Started At UTC**: {datetime.now(timezone.utc).isoformat()}\n")
        report.write(
            f"- **CseGraph Commit**: `{git_value(['rev-parse', '--short=12', 'HEAD']) or 'unknown'}`\n"
        )
        report.write(
            f"- **Git Branch**: `{git_value(['rev-parse', '--abbrev-ref', 'HEAD']) or 'unknown'}`\n"
        )
        report.write(f"- **Python**: `{sys.version.split()[0]}` at `{sys.executable}`\n")
        report.write(f"- **Server Command**: `{command} {' '.join(args)}`\n")
        report.write(f"- **Workload Repository**: `{workload_repo}`\n")
        report.write(f"- **Index Profile**: `{profile}`\n")
        report.write(f"- **Postprocess Level**: `{postprocess_level}`\n")
        report.write(f"- **Context Detail Level**: `{detail_level}`\n")
        report.write(f"- **Query Limit**: {query_limit:,}\n")
        report.write(f"- **Iterations Per Query**: {iterations:,}\n")
        report.write("- **Canonical Metric**: exact UTF-8 bytes\n")
        report.write("- **Heuristic Metric**: CseGraph `chars/4`\n")
        report.write(f"- **OpenAI Proxy Metric**: `{_OPENAI_TOKENIZER_LABEL}`\n\n")
        report.write("## Repository Baseline\n")
        report.write(
            f"- **Repository Size**: {snapshot.files:,} source/text files, "
            f"{format_bytes(snapshot.bytes)}, {snapshot.lines:,} lines\n"
        )
        report.write(f"- **Naive Full-Repo Bytes Per Query**: {snapshot.bytes:,}\n")
        report.write(
            f"- **Naive Full-Repo chars/4 Tokens Per Query**: {snapshot.chars4_tokens:,}\n"
        )
        report.write(
            f"- **Naive Full-Repo OpenAI Proxy Tokens Per Query**: "
            f"{format_optional_int(snapshot.openai_o200k_tokens)}\n"
        )
        report.write(f"- **Naive Full-Repo Read Sample**: {snapshot.full_read_latency_ms:.1f}ms\n")
        report.write(
            f"- **Naive Token Counting Time**: {snapshot.tokenization_latency_ms:.1f}ms "
            "per full-repo sample\n\n"
        )

    async with NativeMcpClient(command, args) as client:
        tool_names = await client.list_tool_names()
        required = {"csegraph_index", "csegraph_context"}
        missing = required - set(tool_names)
        if missing:
            raise RuntimeError(f"MCP server is missing required tools: {sorted(missing)}")

        with report_path.open("a", encoding="utf-8") as report:
            report.write("## MCP Session\n")
            report.write(f"- **MCP Session Startup**: {client.startup_ms:.1f}ms\n")
            report.write(f"- **MCP Tools Exposed**: {', '.join(tool_names)}\n")

        print("Indexing repository through MCP...")
        index_metrics = await client.call_tool(
            "csegraph_index",
            {
                "repo": str(workload_repo),
                "db": str(db_path),
                "profile": profile,
                "postprocess_level": postprocess_level,
            },
        )

        total_calls = len(queries) * iterations
        completed_calls = 0
        for query_index, query in enumerate(queries, 1):
            for iteration in range(1, iterations + 1):
                print(
                    f"\rMCP context call {completed_calls + 1}/{total_calls} "
                    f"(query {query_index}/{len(queries)}, iteration {iteration}/{iterations})...",
                    end="",
                    flush=True,
                )
                try:
                    metrics = await run_context_query(
                        client,
                        query,
                        workload_repo,
                        db_path,
                        profile=profile,
                        detail_level=detail_level,
                    )
                except Exception as exc:
                    errors.append(f"{query}: {exc}")
                    continue
                completed_calls += 1
                mcp_latencies.append(metrics.latency_ms)
                mcp_content_bytes += metrics.content_bytes
                mcp_content_chars4_tokens += metrics.content_chars4_tokens
                mcp_content_openai_o200k_tokens = add_optional(
                    mcp_content_openai_o200k_tokens,
                    metrics.content_openai_o200k_tokens,
                )
                mcp_envelope_chars4_tokens += metrics.envelope_chars4_tokens
                mcp_envelope_openai_o200k_tokens = add_optional(
                    mcp_envelope_openai_o200k_tokens,
                    metrics.envelope_openai_o200k_tokens,
                )
                mcp_tokenization_ms += metrics.tokenization_ms

    print("\nBenchmark calls completed.")

    naive_bytes_total = snapshot.bytes * completed_calls
    naive_chars4_total = snapshot.chars4_tokens * completed_calls
    naive_openai_o200k_total = multiply_optional(snapshot.openai_o200k_tokens, completed_calls)
    chars4_ratio = ratio_optional(naive_chars4_total, mcp_content_chars4_tokens)
    openai_o200k_ratio = ratio_optional(naive_openai_o200k_total, mcp_content_openai_o200k_tokens)

    with report_path.open("a", encoding="utf-8") as report:
        report.write(f"- **Index MCP Round Trip**: {index_metrics.latency_ms:.1f}ms\n\n")
        report.write("## Results\n")
        report.write(f"- **Queries Generated**: {len(queries):,}\n")
        report.write(f"- **Requested MCP Context Calls**: {total_calls:,}\n")
        report.write(f"- **Completed MCP Context Calls**: {completed_calls:,}\n")
        report.write(f"- **Average MCP Context Latency**: {mean(mcp_latencies):.1f}ms\n")
        report.write(f"- **P50 MCP Context Latency**: {percentile(mcp_latencies, 50):.1f}ms\n")
        report.write(f"- **P95 MCP Context Latency**: {percentile(mcp_latencies, 95):.1f}ms\n")
        report.write(
            f"- **MCP Context Token Counting Time**: {mcp_tokenization_ms:.1f}ms "
            "(excluded from MCP latency)\n"
        )
        report.write(f"- **Total Naive Bytes**: {naive_bytes_total:,}\n")
        report.write(f"- **Total MCP Content Bytes**: {mcp_content_bytes:,}\n")
        report.write(f"- **Total Naive chars/4 Tokens**: {naive_chars4_total:,}\n")
        report.write(f"- **Total MCP Content chars/4 Tokens**: {mcp_content_chars4_tokens:,}\n")
        report.write(f"- **Total MCP Envelope chars/4 Tokens**: {mcp_envelope_chars4_tokens:,}\n")
        report.write(f"- **chars/4 Token Efficiency**: {format_optional_ratio(chars4_ratio)}\n")
        report.write(
            f"- **Total Naive OpenAI Proxy Tokens**: "
            f"{format_optional_int(naive_openai_o200k_total)}\n"
        )
        report.write(
            f"- **Total MCP Content OpenAI Proxy Tokens**: "
            f"{format_optional_int(mcp_content_openai_o200k_tokens)}\n"
        )
        report.write(
            f"- **Total MCP Envelope OpenAI Proxy Tokens**: "
            f"{format_optional_int(mcp_envelope_openai_o200k_tokens)}\n"
        )
        report.write(
            f"- **OpenAI Proxy Token Efficiency**: {format_optional_ratio(openai_o200k_ratio)}\n"
        )
        if errors:
            report.write(f"- **Errors**: {len(errors):,}\n")
            for error in errors[:10]:
                report.write(f"  - {error[:300]}\n")
        report.write("\n")

    print(f"Average MCP context latency: {mean(mcp_latencies):.1f}ms")
    print(f"chars/4 token efficiency: {format_optional_ratio(chars4_ratio)}")
    print(f"OpenAI proxy token efficiency: {format_optional_ratio(openai_o200k_ratio)}")
    print(f"Benchmark report written to {report_path}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
