# Agent Context Benchmarks

Benchmarks are repository-maintainer tools. They are not CLI commands and are
not MCP tools.

## Pull-request benchmark

The tracked 20-task fixture covers exact-definition, ambiguity, structural,
debug, and cross-file retrieval.

```bash
env/bin/python tools/run_adaptive_retrieval_benchmark.py \
  --corpus benchmarks/adaptive/pr_tasks.json \
  --modes cold,warm \
  --pyright required \
  --output benchmark_results/adaptive_retrieval.json \
  --fail-on-gates
```

The release gates are:

- 100% target accuracy;
- 100% status accuracy;
- 100% required-evidence recall;
- at least 95% precision;
- median response-token ratio no greater than 35% of the baseline;
- p95 CseGraph overhead below 100 ms.

The baseline is a deterministic `rg --json` plus bounded selective-read
workflow with exact name/path ranking and one-hop import following. CseGraph
and the baseline use the same task corpus and response accounting.

## Balanced nightly corpus

The nightly corpus contains 60 balanced tasks across definition, debug,
refactor, cross-file, and test-impact work. Half of the corpus targets Python
and half targets JavaScript/TypeScript. Repository commits and the corpus digest
are pinned so results are reproducible.

Run the nightly corpus through the same tool:

```bash
env/bin/python tools/run_adaptive_retrieval_benchmark.py \
  --corpus benchmarks/adaptive/nightly_tasks.json \
  --modes cold,warm \
  --pyright required \
  --output benchmark_results/adaptive_nightly.json \
  --fail-on-gates
```

## Repository dogfood gates

A release candidate is indexed from a fresh v11 database and measured against
this repository. Required limits are:

- database size no greater than 40 MiB;
- compressed wheel size no greater than 175 KiB;
- package import no slower than 5 ms;
- package import adds no more than 2 MiB of memory;
- indexing time regresses by no more than 10%.

The first 2.0 release establishes the historical indexing baseline when no
earlier v2 tag exists. Later v2 releases compare their indexing measurements
against the previous v2 tag.

## Report evidence

Reports record:

- corpus digest and pinned repository commits;
- dirty-tree state;
- platform and Python version;
- target, status, recall, precision, and continuation correctness;
- complete serialized-response token counts;
- cold and warm latency;
- baseline completeness;
- database, wheel, import-time, memory, and indexing measurements when
  applicable.

A report is release evidence only when every required task ran, every baseline
result is complete, and every gate was evaluated. Missing data fails closed.
