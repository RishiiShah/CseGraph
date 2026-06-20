# Token Reduction Benchmark

This benchmark measures how much context CseGraph lets an agent avoid reading
when it asks for graph-backed, task-specific context instead of scanning the
whole repository.

## Benchmark Setup

Current performance benchmarks run against cloned repositories under
`sandbox/` using native MCP stdio (`mcp.client` plus
`ClientSession.call_tool`). The CseGraph self-repo tables in this document are
kept as historical/internal regression data because they target CseGraph
symbols such as `ContextService.build_context`.

The self-repo quality corpus now runs through `tools/check_benchmark_regression.py`,
which calls `csegraph_index` and `csegraph_context` over MCP stdio. The older
`tools/csegraph_dev.py benchmark` command remains a maintainer diagnostic for
SDK/internal service measurements and should not be cited as native MCP
benchmark proof.

Index snapshot:

| Metric | Value |
|--------|-------|
| Indexed files | 169 |
| Symbols | 1,935 |
| Edges | 8,498 |
| Parse errors | 0 |
| Raw repository token baseline (chars/4) | 477,278 |

The raw token baseline is the benchmark estimator's `chars/4` count for all
discoverable repository files. Graph context tokens are the source-bearing
context returned for a specific task and target, counted with the same
heuristic. The final verification run measured the current v3 working tree,
including relationship occurrence evidence and import preludes.

## Token Counting Policy

These self-repo tables use CseGraph's transparent `chars/4` heuristic so they
remain comparable with older local harness reports. The native MCP cross-repo
benchmark in `tools/cross_repo_benchmark.py` now reports size metrics
separately:

- **Exact UTF-8 bytes**: canonical provider-neutral payload size.
- **CseGraph `chars/4` tokens**: simple benchmark heuristic.
- **OpenAI proxy tokens**: `tiktoken` with `o200k_base` by default.
- **Claude/Gemini native tokens**: separate provider API audits only.
- **Composer/Cursor native tokens**: not claimed unless a provider-native
  tokenizer/count API is used.

## Token Reduction Results

| Scenario | Profile | Raw repo tokens (chars/4) | Graph context tokens (chars/4) | Reduction | Raw/context ratio | Returned symbols | MCP response bytes | Context call |
|----------|---------|----------------:|---------------------:|----------:|------------------:|---------------:|-------------------:|-------------:|
| Explain `ContextService.build_context` | small | 477,278 | 9,593 | 97.99% | 49.75x | 16 | 30,548 | 49.171 ms |
| Explain `ContextService.build_context` | medium | 477,278 | 15,209 | 96.81% | 31.38x | 54 | 82,690 | 85.366 ms |
| Explain `IndexService.index` | small | 477,278 | 3,178 | 99.33% | 150.18x | 16 | 28,429 | 45.164 ms |

The token-reduction numbers come from the benchmark's source-bearing context
measurement. `Returned symbols`, `MCP response bytes`, and `Context call` come
from the adjacent context retrieval step in the same benchmark run. With
context schema v3, response bytes include explicit `relationships` and
`import_preludes`, not just ranked symbol metadata. Default extracted
relationship fields are compacted when they are redundant, so agents still get
the same graph neighborhood with fewer repeated path and confidence fields.

## Context Quality Check

The self-corpus benchmark verifies that smaller context is still finding the
files, symbols, relationship evidence, and import preludes expected for known
repository tasks, while also guarding against accidentally surfacing forbidden
source bodies in compact responses.

| Metric | Value |
|--------|------:|
| Corpus tasks | 5 |
| Passed tasks | 5 |
| Failed tasks | 0 |
| Overall expected-hit rate | 100% |
| Task pass rate | 100% |
| Sufficient contexts | 5 / 5 |
| Total graph context tokens (chars/4) | 5,635 |
| Average graph context tokens (chars/4) | 1,127.0 |
| Total MCP response bytes | 137,553 |
| Average MCP response bytes | 27,510.6 |
| Tool calls | 8 |

Per-task hits:

| Task | Expected hits | Missing hits | Returned symbols | Context tokens (chars/4) | Response bytes |
|------|--------------:|-------------:|---------------:|---------------:|---------------:|
| `context-build-context` | 14 / 14 | 0 | 16 | 1,214 | 30,557 |
| `index-pipeline` | 13 / 13 | 0 | 16 | 1,291 | 28,440 |
| `discovery-vcs-index` | 7 / 7 | 0 | 12 | 1,056 | 24,560 |
| `minimal-index-health` | 5 / 5 | 0 | 16 | 1,218 | 27,741 |
| `benchmark-pipeline` | 10 / 10 | 0 | 16 | 856 | 26,255 |

## What This Shows

CseGraph reduces token use by routing the agent through indexed code structure:

- A targeted `small` profile request returned 3,178 to 9,593 `chars/4`
  context tokens instead of the 477,278 `chars/4` raw repository baseline.
- The measured reduction was 97.99% to 99.33% for the `small` profile target
  runs.
- The `medium` profile widened retrieval from 16 symbols to 54 symbols while
  still reducing context by 96.81%.
- The corpus run kept a 100% expected-hit rate and 5 / 5 sufficient contexts,
  so the reduction did not come from dropping the expected files, symbols,
  relationship evidence, occurrence snippets, or from masking insufficient
  context.

For agent workflows, this means CseGraph can usually answer "what should I
read next?" with a compact graph neighborhood before the agent opens large
files or asks for broader source.

## Reproduction Commands

```bash
CSEGRAPH_BENCH_REPOS=flask CSEGRAPH_BENCH_QUERY_LIMIT=100 \
  CSEGRAPH_CROSS_REPO_REPORT=.scratch/csegraph/native_mcp_sandbox_flask.md \
  CSEGRAPH_CROSS_REPO_JSON=.scratch/csegraph/native_mcp_sandbox_flask.json \
  env/bin/python tools/cross_repo_benchmark.py

env/bin/python tools/check_benchmark_regression.py --repo . \
  --corpus benchmarks/context_quality/csegraph_self.json \
  --profile small

CSEGRAPH_FULL_BENCH_PROFILES=small,medium,large \
  env/bin/python tools/run_full_mcp_benchmark.py
```

## Caveats

These are benchmark-estimated `chars/4` tokens, not model-provider billing
tokens. Results will vary with repository size, profile, target, and query
specificity. This report measures the current working tree, so values can move
as parser, schema, and retrieval behavior changes.

## Codex Rerun — 2026-06-20

This section appends the current rerun from Codex. The raw JSON artifact is
`.scratch/csegraph/benchmark_results.json` with timestamp
`2026-06-20T15:22:47-0400`.

The results below are repo-local CseGraph benchmark results. They measure
CseGraph context retrieval through the benchmark harness, not a full
client-specific session from Codex, Claude Code, or Antigravity.

Current index snapshot:

| Metric | Value |
|--------|------:|
| Indexed files | 175 |
| Symbols | 1,972 |
| Edges | 8,695 |
| Raw repository token baseline (chars/4) | 492,670 |

### Best, Worst, And Average Scenarios

Best cases by raw/context token ratio:

| Scenario | Profile | Raw/context ratio | Reduction | Context tokens (chars/4) | Context call |
|----------|---------|------------------:|----------:|---------------:|-------------:|
| `MinimalService.first` | small | **221.23x** | 99.55% | 2,227 | 65.379 ms |
| `IndexService.index` | small | **155.81x** | 99.36% | 3,162 | 71.722 ms |
| `GraphQueryService.neighborhood` | small | **122.04x** | 99.18% | 4,037 | 67.756 ms |

Worst cases by raw/context token ratio:

| Scenario | Profile | Raw/context ratio | Reduction | Context tokens (chars/4) | Context call |
|----------|---------|------------------:|----------:|---------------:|-------------:|
| `ContextService.build_context` | large | **24.45x** | 95.91% | 20,152 | 109.848 ms |
| `ContextService.build_context` | medium | **30.41x** | 96.71% | 16,199 | 105.052 ms |
| `RefreshService.refresh` | large | **35.90x** | 97.21% | 13,724 | 100.818 ms |

Average by scenario across `small`, `medium`, and `large` profiles:

| Scenario | Average ratio | Average reduction | Average context tokens (chars/4) | Average context call |
|----------|--------------:|------------------:|-----------------------:|---------------------:|
| `MinimalService.first` | **119.20x** | 98.71% | 6,361 | 87.0 ms |
| `IndexService.index` | **81.20x** | 98.27% | 8,537 | 95.0 ms |
| `GraphQueryService.neighborhood` | **76.42x** | 98.42% | 7,772 | 89.7 ms |
| `RefreshService.refresh` | **52.53x** | 97.77% | 10,993 | 89.3 ms |
| `ContextService.build_context` | **35.12x** | 96.88% | 15,369 | 96.9 ms |

### Current Corpus Quality

The current corpus quality run is **4 / 5 passed** for every profile, while all
5 contexts are still marked sufficient. The pass rule is strict: a corpus task
passes only when its expected-hit rate is exactly `1.0`.

| Profile | Passed tasks | Overall expected-hit rate | Sufficient contexts | Failing task | Failing task hits |
|---------|-------------:|--------------------------:|--------------------:|--------------|------------------:|
| small | 4 / 5 | 93.88% | 5 / 5 | `context-build-context` | 11 / 14 |
| medium | 4 / 5 | 97.96% | 5 / 5 | `context-build-context` | 13 / 14 |
| large | 4 / 5 | 97.96% | 5 / 5 | `context-build-context` | 13 / 14 |

The missing evidence is concentrated in `context-build-context`. In the small
profile, the run missed `csegraph/_core/retrieval/scoring.py`,
`apply_graph_expansion`, and the import relationship from `context.py` to
`scoring.py`. In the medium and large profiles, the only missed expected item
was `apply_graph_expansion`. Because `sufficient` remained true for all 5 tasks,
the current 4 / 5 score means "one expected evidence checklist item was missed,"
not "one task returned unusable context."

## Full Suite Rerun — 2026-06-20 17:29 EDT

The full suite was rerun from Codex and wrote
`.scratch/csegraph/benchmark_results.json` with timestamp
`2026-06-20T17:29:03-0400`. This remains an internal CseGraph benchmark suite,
not a full client-specific Codex, Claude Code, or Antigravity session. The
corpus regression gate itself now runs through MCP stdio via
`tools/check_benchmark_regression.py`.

Current index snapshot:

| Metric | Value |
|--------|------:|
| Indexed files | 175 |
| Symbols | 2,026 |
| Edges | 8,952 |
| Raw repository token baseline (chars/4) | 508,464 |

### Best, Worst, And Average Scenarios

Best cases by raw/context token ratio:

| Scenario | Profile | Raw/context ratio | Reduction | Context tokens (chars/4) | Context call |
|----------|---------|------------------:|----------:|---------------:|-------------:|
| `MinimalService.first` | small | **188.18x** | 99.47% | 2,702 | 66.764 ms |
| `GraphQueryService.neighborhood` | small | **124.35x** | 99.20% | 4,089 | 70.546 ms |
| `MinimalService.first` | medium | **98.10x** | 98.98% | 5,183 | 98.506 ms |

Worst cases by raw/context token ratio:

| Scenario | Profile | Raw/context ratio | Reduction | Context tokens (chars/4) | Context call |
|----------|---------|------------------:|----------:|---------------:|-------------:|
| `ContextService.build_context` | large | **24.12x** | 95.85% | 21,082 | 112.359 ms |
| `ContextService.build_context` | medium | **32.63x** | 96.94% | 15,581 | 108.314 ms |
| `RefreshService.refresh` | large | **36.16x** | 97.23% | 14,060 | 103.783 ms |

Average by scenario across `small`, `medium`, and `large` profiles:

| Scenario | Average ratio | Average reduction | Average context tokens (chars/4) | Average context call |
|----------|--------------:|------------------:|-----------------------:|---------------------:|
| `MinimalService.first` | **110.10x** | 98.73% | 6,478 | 88.8 ms |
| `GraphQueryService.neighborhood` | **77.87x** | 98.45% | 7,864 | 92.2 ms |
| `RefreshService.refresh` | **53.61x** | 97.79% | 11,217 | 90.6 ms |
| `IndexService.index` | **52.97x** | 98.05% | 9,903 | 97.0 ms |
| `ContextService.build_context` | **38.64x** | 97.03% | 15,086 | 99.6 ms |

### Current Corpus Quality

The current corpus quality run is **5 / 5 passed** for every profile, with all
5 contexts marked sufficient. The strict pass rule requires every
expected file, symbol, relationship, occurrence snippet, and import prelude to
be found.

| Profile | Passed tasks | Overall expected-hit rate | Sufficient contexts | Total context tokens (chars/4) | Notes |
|---------|-------------:|--------------------------:|--------------------:|-------------------------------:|-------|
| small | 5 / 5 | 100.00% | 5 / 5 | 5,791 | `context-build-context` now hits 14 / 14 |
| medium | 5 / 5 | 100.00% | 5 / 5 | 15,152 | `context-build-context` now hits 14 / 14 |
| large | 5 / 5 | 100.00% | 5 / 5 | 16,967 | `context-build-context` now hits 14 / 14 |

The previous failure had two causes: the corpus expected the old
`apply_graph_expansion` symbol while production uses
`apply_graph_expansion_from_maps`, and small-profile retrieval could starve
direct callees imported from another file. The corpus now expects the production
symbol, and retrieval reserves direct imported callees early enough for
`scoring.py` to appear in the small context.
