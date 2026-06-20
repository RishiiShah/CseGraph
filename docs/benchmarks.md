# Agent Context Benchmarks

This document outlines the context-size and latency performance of the **CseGraph MCP** server. It serves as a benchmark baseline for autonomous coding agents (such as Antigravity, Claude Code, and Cursor).

Because CseGraph routes agents through an indexed structural graph rather than relying on naive recursive file scraping (`rglob`), it drastically reduces the tokens an LLM must process, leading to substantially cheaper and faster agentic workflows.

## Methodology & Proof

The current reproducible suite lives at `tools/cross_repo_benchmark.py`. It
launches the CseGraph MCP server as a separate stdio process and calls tools
through the official `mcp.client` JSON-RPC path, which is the same transport
shape used by coding agents.

1. **Repository Selection**: We cloned 10 distinct, major open-source repositories (at `--depth 1`) to represent varying architectural domains (SWE, ML/AI, Backend, Data Science).
2. **Procedural Query Generation**: The benchmark scans repository source/text files outside the CseGraph engine and extracts actual class names, functions, and file paths to dynamically generate **1,000 uniquely targeted queries** (100 per repository).
3. **Phase A (Clean Context)**: Each generated query is sent to the CseGraph MCP server over stdio. The naive baseline is a full read of all included source/text files, multiplied once per completed MCP context call. We record exact UTF-8 bytes, separated token estimates, and MCP round-trip latency.
4. **Phase B (Active Mutation)**: To test invalidation, a source file in each repository is programmatically mutated, `csegraph_refresh` is called through MCP, and the repository is restored. The MCP server detects the fingerprint change, executes an incremental SQLite refresh, and returns updated graph context.

*You can run this suite locally on your machine:*
```bash
env/bin/python tools/cross_repo_benchmark.py
```

By default, the report is written to
`benchmark_results/native_mcp_cross_repo_results.md` and the machine-readable
artifact is written to `benchmark_results/native_mcp_cross_repo_results.json`.
To write them elsewhere, set `CSEGRAPH_CROSS_REPO_REPORT` and
`CSEGRAPH_CROSS_REPO_JSON`:

```bash
CSEGRAPH_CROSS_REPO_REPORT=benchmark_results/native_mcp_cross_repo_results.md \
  CSEGRAPH_CROSS_REPO_JSON=benchmark_results/native_mcp_cross_repo_results.json \
  env/bin/python tools/cross_repo_benchmark.py
```

For OpenAI proxy token counts, install the benchmark extra:

```bash
env/bin/python -m pip install -e ".[benchmark]"
```

## Token Counting Policy

CseGraph benchmark reports separate size metrics instead of presenting one
token number as universal across model providers:

- **Exact UTF-8 bytes** are the canonical provider-neutral metric.
- **CseGraph `chars/4` tokens** are the simple transparent heuristic used by
  older local harnesses and kept for continuity with prior reports.
- **OpenAI proxy tokens** use `tiktoken` with `o200k_base` by default. This is
  useful for GPT-4o, GPT-5, o-series, and other OpenAI-family comparisons, but
  it is not a Claude, Gemini, Composer, or Cursor native count.
- **Claude and Gemini provider-native tokens** require separate provider API
  audits, such as Anthropic token counting or Google `count_tokens`.
- **Composer/Cursor exact tokenizer counts** are not labeled as exact here
  unless a public provider-native tokenizer/count API is used.

## Token Reduction Results (Phase A, chars/4)

The table below is a historical `chars/4` snapshot kept for continuity. Rerun
`tools/cross_repo_benchmark.py` for current native MCP stdio reports with exact
bytes, `chars/4`, and OpenAI proxy tokens split out.

| Repository | Profile | Naive Context Tokens (chars/4) | CseGraph MCP Tokens (chars/4) | Context Reduction |
|------------|---------|---------------------:|----------------------:|------------------:|
| `transformers` | ML/AI | 1,661,279,209 | 1,670,208 | **994.7x** |
| `django` | SWE/Web | 413,314,057 | 2,009,028 | **205.7x** |
| `scikit-learn` | ML/AI | 352,404,048 | 1,786,873 | **197.2x** |
| `pandas` | Data Science | 525,603,239 | 6,947,259 | **75.7x** |
| `fastapi` | SWE/Web | 76,894,962 | 1,491,136 | **51.6x** |
| `pytest` | Testing | 87,197,455 | 1,788,802 | **48.7x** |
| `celery` | Backend | 76,166,543 | 1,719,187 | **44.3x** |
| `flask` | SWE/Web | 13,447,553 | 1,089,903 | **12.3x** |
| `nanoGPT` | ML/AI | 1,052,545 | 258,886 | **4.1x** |
| `micrograd` | Micro-repo | 111,762 | 264,574 | **0.4x** |

### Key Findings

1. **Massive Efficiency at Scale**: For large enterprise repositories like HuggingFace `transformers` and `django`, agents utilizing naive file-reading will completely blow past any standard LLM context window (reaching up to 1.6 Billion tokens). CseGraph precision-targets the relevant paths, shrinking context down to just 1-2 million tokens.
2. **Micro-Repo Overhead**: For extremely small, single-file codebases (like `micrograd`), CseGraph introduces slight token overhead compared to reading the raw file, because the structural graph relationships explicitly expand the data map. This is completely expected and represents the absolute worst-case scenario.

## Cache Invalidation Latency (Phase B)

After agents read context, they typically edit files. To evaluate how quickly CseGraph invalidates its process-local cache and re-indexes the graph after a file modification, the benchmark runs an active mutation phase on all 10 repositories.

| Repository | Mutation Invalidation Latency (Local) |
|------------|--------------------------------------:|
| `transformers` | ~4.7 seconds |
| `django` | ~2.9 seconds |
| `pandas` | ~2.8 seconds |
| `scikit-learn` | ~0.9 seconds |
| `celery` | ~0.5 seconds |
| `fastapi` | ~0.2 seconds |

Even on massive repositories with tens of thousands of files, CseGraph's incremental SQLite refresh bounds the maximum latency to under 5 seconds, ensuring the agent is never waiting long for a fresh snapshot.

## Codex Rerun — 2026-06-20

The following data was rerun locally from Codex on 2026-06-20 using
`tools/cross_repo_benchmark.py`. The raw report is stored at
`.scratch/csegraph/codex_cross_repo_benchmark_results_20260620_1453.md`.

This historical table was produced before the native MCP stdio refactor. It
called the CseGraph MCP handler directly through repo code and compared it to a
naive Python source scan. Keep it only as historical `chars/4` context; rerun
`tools/cross_repo_benchmark.py` for current native MCP stdio results.

| Repository | Naive tokens (chars/4) | CseGraph tokens (chars/4) | Reduction | Avg naive latency | Avg MCP latency | Mutation latency |
|------------|-------------:|----------------:|----------:|------------------:|----------------:|-----------------:|
| `transformers` | 1,661,279,209 | 1,623,198 | **1023.5x** | 283.5 ms | 4,641.7 ms | 4,416.4 ms |
| `django` | 413,314,057 | 1,941,804 | **212.9x** | 165.6 ms | 2,771.8 ms | 2,844.7 ms |
| `scikit-learn` | 352,404,048 | 1,750,819 | **201.3x** | 49.2 ms | 861.5 ms | 1,070.7 ms |
| `pandas` | 525,603,239 | 6,670,649 | **78.8x** | 81.1 ms | 2,715.7 ms | 2,673.3 ms |
| `pytest` | 87,197,455 | 1,423,966 | **61.2x** | 14.8 ms | 423.7 ms | 407.9 ms |
| `fastapi` | 76,894,962 | 1,544,410 | **49.8x** | 34.6 ms | 291.2 ms | 287.4 ms |
| `celery` | 76,166,543 | 1,759,995 | **43.3x** | 16.9 ms | 463.4 ms | 456.3 ms |
| `flask` | 13,447,553 | 1,134,696 | **11.9x** | 4.1 ms | 56.1 ms | 60.5 ms |
| `nanoGPT` | 1,052,545 | 259,645 | **4.1x** | 1.0 ms | 5.5 ms | 3.4 ms |
| `micrograd` | 111,762 | 265,733 | **0.4x** | 0.8 ms | 4.8 ms | 4.0 ms |

Best case by token reduction was `transformers` at **1023.5x**. Worst case was
`micrograd` at **0.4x**, where graph metadata overhead exceeded the tiny raw
repository baseline. Across all 10 repositories, aggregate token reduction was
**174.6x** (`3,207,471,373` naive chars/4 tokens versus `18,374,915`
CseGraph chars/4 tokens).
The unweighted average repository reduction was **168.7x**, and average
mutation invalidation latency was **1,222.5 ms**.

## Agent Client MCP Differences

Different agent clients do not expose identical MCP behavior, so harness
numbers should not be described as "Codex results" or "Claude results" unless
the client itself was part of the measurement.

- **Codex**: Official Codex docs state that Codex supports MCP servers in the
  CLI and IDE extension, with local stdio servers and Streamable HTTP servers.
  Codex stores MCP configuration in `config.toml`, supports server
  instructions, bearer token and OAuth authentication for HTTP servers, tool
  allow/deny lists, per-server timeouts, and per-tool approval policy.
  Source: https://developers.openai.com/codex/mcp
- **Claude Code**: Official Claude Code docs describe HTTP, deprecated SSE,
  local stdio, and WebSocket MCP transports. Claude also documents MCP scopes
  (`local`, `project`, `user`), dynamic tool updates, automatic reconnection for
  HTTP/SSE, push-message channels, plugin-provided MCP servers, MCP output
  warnings above 10,000 tokens, MCP resources, MCP prompts, and default Tool
  Search behavior. Source: https://docs.claude.com/en/docs/claude-code/mcp
- **Google Antigravity**: The public Antigravity homepage was checked on
  2026-06-20, but no official, crawlable MCP client behavior reference was
  found. Do not claim Antigravity-specific MCP calling semantics from these
  CseGraph harness results without a separately captured Antigravity run or an
  official Antigravity MCP reference. Source checked:
  https://antigravity.google/

The transport-level MCP specification also matters: for Streamable HTTP,
session IDs, SSE streams, reconnection/resumability, and protocol-version
headers are part of the client/server contract. Source:
https://modelcontextprotocol.io/specification/2025-06-18/basic/transports

## Sandbox 100-Query Harness

The 100-query harness now also uses native MCP stdio. It launches the
CseGraph server as a subprocess, calls `csegraph_index` and
`csegraph_context` over JSON-RPC, and reports exact bytes, `chars/4`, and
OpenAI proxy tokens separately. Its default workload is a repository under
`sandbox/`, not the CseGraph engine source tree. Set `CSEGRAPH_100_REPO` to
choose a specific workload repository.

Run it with:

```bash
CSEGRAPH_100_QUERIES_REPORT=benchmark_results/native_mcp_100_queries.md \
  env/bin/python tools/run_100_queries_benchmark.py
```

Legacy entrypoints are kept as wrappers:

```bash
env/bin/python tools/compare_mcp_benchmark.py
env/bin/python tools/deep_mcp_benchmark.py
```

The historical rerun below predates the native MCP stdio refactor and should
not be cited as protocol-level MCP latency:

| Metric | Value |
|--------|------:|
| Unique queries | 100 |
| Iterations per query | 3 |
| Total naive tokens (chars/4) | 2,617,473,541 |
| Total CseGraph tokens (chars/4) | 1,140,024 |
| Token reduction | **2296.0x** |
| Average naive latency | 838.8 ms |
| Average MCP latency | 112.9 ms |

Because the historical table used generated queries and the repo-local Python
handler, the `2296.0x` number should be cited only as "historical local
100-query CseGraph handler harness" data, not as protocol-level MCP, provider
billing-token, or real-world Codex/Claude/Antigravity usage.

## Native MCP Full Suite — 2026-06-20

The following run was executed from Codex on 2026-06-20 after the native MCP
stdio refactor. It used `mcp.client` JSON-RPC calls against a spawned
`csegraph._cli serve` process, not direct imports of CseGraph internals.
Going forward, performance benchmark runs use repositories under `sandbox/`.
The self-repo corpus check remains a quality regression because the corpus
asserts CseGraph-specific symbols and relationships.

Artifacts:

- `benchmark_results/native_mcp_compare.md`
- `benchmark_results/native_mcp_100_queries_codex_20260620.md`
- `benchmark_results/native_mcp_deep.md`
- `benchmark_results/native_mcp_cross_repo_codex_20260620.md`
- `benchmark_results/native_mcp_cross_repo_results.json`
- `.scratch/csegraph/benchmark_results.json`

Validation:

- `env/bin/python -m py_compile csegraph/_core/retrieval/context.py tools/check_benchmark_regression.py tools/cross_repo_benchmark.py tools/run_100_queries_benchmark.py tools/compare_mcp_benchmark.py tools/deep_mcp_benchmark.py`
- `git diff --check`
- `env/bin/python tools/compare_mcp_benchmark.py`
- `env/bin/python tools/run_100_queries_benchmark.py`
- `env/bin/python tools/cross_repo_benchmark.py`
- `env/bin/python tools/deep_mcp_benchmark.py`
- `env/bin/python tools/run_full_mcp_benchmark.py`
- `env/bin/python tools/check_benchmark_regression.py --repo .`
- `env/bin/python tools/check_benchmark_regression.py --repo . --profile medium --max-avg-context-tokens 4000 --max-avg-response-bytes 70000 --max-returned-node-count 60`
- `env/bin/python tools/check_benchmark_regression.py --repo . --profile large --max-avg-context-tokens 5000 --max-avg-response-bytes 80000 --max-returned-node-count 80`

The regression check now runs the corpus through MCP stdio rather than calling
`BenchmarkService` directly in-process. After the retrieval fix, the corpus
passes for `small`, `medium`, and `large`: 5 / 5 tasks, 100% expected-hit rate,
and 5 / 5 sufficient contexts in each profile.

### Native MCP Self-Repo Runs

| Suite | Calls | Avg MCP latency | P50 | P95 | Naive chars/4 | MCP chars/4 | chars/4 reduction | Naive OpenAI proxy | MCP OpenAI proxy | OpenAI proxy reduction |
|-------|------:|----------------:|----:|----:|--------------:|------------:|-------------------:|-------------------:|-----------------:|-----------------------:|
| Smoke wrapper | 4 | 134.0 ms | n/a | n/a | n/a | n/a | 19.1x | n/a | n/a | 17.7x |
| 100-query | 100 | 129.0 ms | 132.4 ms | 151.4 ms | 37,694,800 | 1,698,882 | **22.2x** | 35,205,300 | 1,712,023 | **20.6x** |
| Deep wrapper | 30 | 132.1 ms | 133.0 ms | 142.6 ms | 11,308,620 | 631,005 | **17.9x** | 10,561,770 | 631,935 | **16.7x** |

The self-repo native MCP reports used `tiktoken:encoding=o200k_base` only as an
OpenAI-family proxy. They should not be presented as Claude, Gemini, Composer,
Cursor, Codex billing, or Antigravity billing token counts.

### Native MCP Cross-Repo Aggregate

The full cross-repo native MCP run completed 1,000 / 1,000 context calls across
10 repositories, plus one mutation refresh phase per repository.

| Metric | Value |
|--------|------:|
| Total naive chars/4 tokens | 4,328,147,800 |
| Total MCP content chars/4 tokens | 17,534,557 |
| Aggregate chars/4 reduction | **246.8x** |
| Total naive OpenAI proxy tokens | 4,201,341,600 |
| Total MCP content OpenAI proxy tokens | 18,167,540 |
| Aggregate OpenAI proxy reduction | **231.3x** |
| Unweighted average repo chars/4 reduction | **221.4x** |
| Unweighted average MCP context latency | 1,144.1 ms |
| Unweighted average Phase B mutation round trip | 16,411.5 ms |

Per-repository native MCP results:

| Repository | Queries | chars/4 reduction | OpenAI proxy reduction | Avg MCP latency | P95 MCP latency | Phase B total |
|------------|--------:|-------------------:|-----------------------:|----------------:|----------------:|--------------:|
| `transformers` | 100 / 100 | **1219.1x** | **1118.9x** | 4,413.0 ms | 5,666.8 ms | 112,393.2 ms |
| `django` | 100 / 100 | **319.1x** | **291.9x** | 2,476.6 ms | 3,062.9 ms | 26,679.1 ms |
| `fastapi` | 100 / 100 | **212.8x** | **225.1x** | 272.8 ms | 322.0 ms | 2,333.2 ms |
| `pandas` | 100 / 100 | **192.0x** | **191.9x** | 2,520.2 ms | 3,011.7 ms | 12,897.3 ms |
| `scikit-learn` | 100 / 100 | **171.1x** | **164.6x** | 870.3 ms | 1,064.8 ms | 4,361.1 ms |
| `pytest` | 100 / 100 | **42.5x** | **37.2x** | 383.9 ms | 463.2 ms | 2,012.9 ms |
| `celery` | 100 / 100 | **41.2x** | **35.7x** | 431.9 ms | 507.8 ms | 2,674.0 ms |
| `flask` | 100 / 100 | **10.4x** | **9.5x** | 59.5 ms | 97.0 ms | 575.1 ms |
| `nanoGPT` | 100 / 100 | **5.2x** | **4.9x** | 7.2 ms | 10.5 ms | 93.4 ms |
| `micrograd` | 100 / 100 | **0.6x** | **0.6x** | 5.6 ms | 6.7 ms | 96.1 ms |

Best case was `transformers`: large raw baseline and compact graph context
produced **1219.1x chars/4** reduction. Worst case was `micrograd`: the raw
repository is so small that MCP content plus graph metadata was larger than a
full naive read, producing **0.6x**.
