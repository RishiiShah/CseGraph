# Token Reduction Benchmark

This benchmark measures how much context CseGraph lets an agent avoid reading
when it asks for graph-backed, task-specific context instead of scanning the
whole repository.

## Benchmark Setup

Measured on the CseGraph repository using `tools/csegraph_dev.py benchmark`.
The benchmark indexed the current working tree into ignored databases under
`.scratch/csegraph/`.

Index snapshot:

| Metric | Value |
|--------|-------|
| Indexed files | 169 |
| Symbols | 1,935 |
| Edges | 8,498 |
| Parse errors | 0 |
| Raw repository token baseline | 477,278 |

The raw token baseline is the benchmark estimator's count for all discoverable
repository files. Graph context tokens are the source-bearing context returned
for a specific task and target. The final verification run measured the current
v3 working tree, including relationship occurrence evidence and import preludes.

## Token Reduction Results

| Scenario | Profile | Raw repo tokens | Graph context tokens | Reduction | Raw/context ratio | Returned symbols | MCP response bytes | Context call |
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
| Total graph context tokens | 5,635 |
| Average graph context tokens | 1,127.0 |
| Total MCP response bytes | 137,553 |
| Average MCP response bytes | 27,510.6 |
| Tool calls | 8 |

Per-task hits:

| Task | Expected hits | Missing hits | Returned symbols | Context tokens | Response bytes |
|------|--------------:|-------------:|---------------:|---------------:|---------------:|
| `context-build-context` | 14 / 14 | 0 | 16 | 1,214 | 30,557 |
| `index-pipeline` | 13 / 13 | 0 | 16 | 1,291 | 28,440 |
| `discovery-vcs-index` | 7 / 7 | 0 | 12 | 1,056 | 24,560 |
| `minimal-index-health` | 5 / 5 | 0 | 16 | 1,218 | 27,741 |
| `benchmark-pipeline` | 10 / 10 | 0 | 16 | 856 | 26,255 |

## What This Shows

CseGraph reduces token use by routing the agent through indexed code structure:

- A targeted `small` profile request returned 3,178 to 9,593 context tokens
  instead of the 477,278-token raw repository baseline.
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
env/bin/python tools/csegraph_dev.py benchmark . \
  --db .scratch/csegraph/benchmark-context-small.db \
  --profile small \
  --query "How does ContextService build graph-backed context for a target?" \
  --target ContextService.build_context \
  --json

env/bin/python tools/csegraph_dev.py benchmark . \
  --db .scratch/csegraph/benchmark-context-medium.db \
  --profile medium \
  --query "How does ContextService build graph-backed context for a target?" \
  --target ContextService.build_context \
  --json

env/bin/python tools/csegraph_dev.py benchmark . \
  --db .scratch/csegraph/benchmark-index-small.db \
  --profile small \
  --query "How does IndexService index a repository and write parsed symbols?" \
  --target IndexService.index \
  --json

env/bin/python tools/csegraph_dev.py benchmark . \
  --db .scratch/csegraph/benchmark-corpus-small.db \
  --corpus benchmarks/context_quality/csegraph_self.json \
  --profile small \
  --json
```

## Caveats

These are benchmark-estimated tokens, not model-provider billing tokens. Results
will vary with repository size, profile, target, and query specificity. This
report measures the current working tree, so values can move as parser, schema,
and retrieval behavior changes.
