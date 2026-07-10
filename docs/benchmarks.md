# Benchmark Evidence

This page tracks the adaptive retrieval benchmark evidence used for CseGraph
performance work. The current benchmark compares CseGraph's adaptive retrieval
against a bounded `rg`/selective-read baseline under the same token budget.

## Method

- MCP latency is measured as the client-side round trip around
  `session.call_tool(...)`; token counting and report writing are excluded.
- Token reduction is measured against the bounded baseline used by the adaptive
  retrieval runner, not against a full-repository read.
- The old full-repository footprint sweep was removed from this page because
  its 99.xx% reductions were easy to misread as agent-realistic savings.

## Adaptive Retrieval Benchmark Tiers

The adaptive retrieval runner is the current correctness-and-efficiency
benchmark for agent context selection. It compares CseGraph's adaptive retrieval
against a bounded `rg`/selective-read baseline under the same token budget, and
reports status accuracy, target recall, slice precision, token use, index
diagnostics, workspace hygiene, and latency. The runner writes
`csegraph-adaptive-retrieval-report-v4` reports.

Only the PR, nightly, and release corpora are tracked in git. The perf/stress
and broad corpora are generated locally from the tracked release seed via
`tools/generate_sandbox_stress_corpus.py` and are intentionally ignored.

Current corpus tiers:

| Tier | Corpus | Purpose | Current scope |
|---|---|---|---|
| PR | `benchmarks/adaptive/pr_tasks.json` | Fast regression gate for expected status, recall, precision, token budget, and quality mix | Local fixtures |
| Nightly | `benchmarks/adaptive/nightly_tasks.json` | Broader fixture regression with more task categories | Local fixtures |
| Release | `benchmarks/adaptive/sandbox_release_tasks.json` | Hand-curated real-repo quality gate with ambiguous, targetless, structural, insufficient-budget, and test-evidence tasks | Tracked seed |
| Perf/stress | `benchmarks/adaptive/sandbox_stress_tasks.json` | High-N stable latency/token averages on representative small/medium/large repos | Generated locally |
| Broad | `benchmarks/adaptive/sandbox_broad_tasks.json` | All-local-sandbox coverage for ecosystem shape and scale cliffs | Generated locally |

### Broad all-sandbox run

Latest local broad run: `2026-07-10`, cold mode, Pyright off, 800-token budget.
The report measured all 348 broad-corpus tasks across all 10 local sandbox
repositories.

| Metric | Result |
|---|---:|
| Tasks measured | 348 / 348 |
| Expected status accuracy | 100.0% |
| Adaptive recall | 100.0% |
| Adaptive precision | 100.0% |
| Adaptive role recall | 100.0% |
| Adaptive median tokens | 198.5 |
| Baseline median tokens | 718.5 |
| Adaptive / baseline token ratio | 27.63% |
| Median token reduction | 72.37% |

The broad corpus used a temporary pilot cap, not a principled final weighting.
The cap existed only to make the first all-10 local run tractable while
validating repository hygiene, indexing, report schema, and task correctness.
It should not be used to compare large repos with small repos.

The readout is intentionally mixed:

- Retrieval quality passed: every broad task matched the expected status, target
  recall, precision, and role recall.
- Token efficiency is strong: the broad run reduced median context tokens by
  72.37% versus the bounded baseline.
- The latency gate did not pass: `engine_p95_overhead_below_100ms` failed,
  driven mostly by `transformers`, with secondary cliffs in `pandas`, `django`,
  and `scikit-learn`.
- The broad tier is therefore useful as a perf/nightly signal, not as a PR
  gate.
- The broad tier is not the final benchmark shape because most repositories were
  capped at 40 tasks regardless of size.

### Corpus realism policy

All benchmark tasks should be hard to fool and representative of how agents
actually ask for context.

Repository tasks should be tailored to each project, not only generated from
generic symbol lookups. Generated exact-definition tasks are still useful for
stable latency/token averages, but every serious release/perf corpus should also
include repository-specific scenarios:

- `micrograd`: autograd graph traversal, `Value.backward`, operation closures,
  simple neural-network helpers, and small test-impact tasks.
- `flask`: routing, blueprints, request/app contexts, CLI behavior, error
  handling, and tests around dispatch.
- `django`: URL resolving, shortcuts, model/query behavior, forms/admin, test
  discovery, and cross-file framework flows.
- `fastapi`: dependency injection, routing, OpenAPI/schema generation,
  validation, and async/test-client paths.
- `pytest`: collection, fixtures, assertion rewriting, hooks/plugins, and
  regression tests under `testing/`.
- `celery`: task registration, app finalization, worker/beat control, result
  backends, routing, and integration-style tests.
- `pandas`: dataframe/array operations, dtype dispatch, IO/parsing, groupby,
  extension arrays, and targeted regression tests.
- `scikit-learn`: estimators, validation utilities, datasets, metrics,
  pipelines, and model-selection flows.
- `transformers`: model/config loading, tokenizers, generation, pipelines,
  CLI/serving, dependency gates, and tests that exercise large module layouts.
- `nanoGPT`: training loop, batching, model configuration, optimizer setup, and
  data preparation scripts.

Task counts should also scale with repository size and complexity, with caps so
large repositories do not dominate the aggregate. The report should keep both:

- weighted aggregate metrics, where more tasks in larger repositories reflect
  real surface area; and
- macro-average metrics, where each repository gets one vote so `transformers`,
  `pandas`, and `django` cannot hide regressions in smaller projects.

There is no benchmark-quality reason for large repos to stop at 40 tasks. The
current broad corpus used 40 as a first-pass execution cap only. Large repos
should have more tasks because they expose more APIs, more architecture shapes,
more duplicate names, more tests, and more performance cliffs. Small repos
should have fewer but more scenario-specific tasks.

The next specialized corpus should be size-weighted and repo-specific:

| Repository | Approx. Python files | Approx. symbols | Current broad tasks | Target specialized tasks | Why |
|---|---:|---:|---:|---:|---|
| `micrograd` | 5 | 40 | 20 | 20-25 | Tiny repo; scenario variety matters more than count |
| `nanoGPT` | 15 | 30 | 8 | 20-30 | Needs tailored training/data/model tasks instead of only unique-symbol tasks |
| `flask` | 83 | 1,620 | 40 | 60-80 | Medium framework with routing, context, CLI, and tests |
| `pytest` | 270 | 6,850 | 40 | 80-100 | Plugin/hook/collection behavior deserves more tailored coverage |
| `celery` | 416 | 8,875 | 40 | 90-120 | Worker, beat, task registry, routing, and backend paths |
| `fastapi` | 1,129 | 5,556 | 40 | 90-120 | Routing, dependency injection, validation, OpenAPI, async tests |
| `scikit-learn` | 1,014 | 12,744 | 40 | 120-150 | Estimator/pipeline/dataset/model-selection flows |
| `pandas` | 1,509 | 33,139 | 40 | 160-200 | Large API surface, dtype dispatch, IO, arrays, groupby, tests |
| `django` | 2,924 | 43,431 | 40 | 180-220 | Large framework with many cross-file subsystem flows |
| `transformers` | 4,559 | 73,598 | 40 | 220-300 | Largest repo and current p95 latency cliff |

Specialized benchmark suites should be split by purpose:

- `sandbox_broad`: all repos, capped, fast enough for regular perf/nightly
  visibility.
- `sandbox_specialist`: size-weighted, repo-tailored scenarios for release
  quality.
- `sandbox_mega`: expensive separate stress suites for `transformers`, `django`,
  `pandas`, and `scikit-learn`, so large-repo latency cliffs cannot be hidden or
  block smaller-repo feedback.
- task-type suites for structural, test-impact, ambiguous, insufficient-budget,
  and agent-edit cases, because those failure modes are different from exact
  definition lookup.
