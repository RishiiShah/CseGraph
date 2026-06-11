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
| Indexed files | 158 |
| Symbols | 1,729 |
| Edges | 7,717 |
| Parse errors | 0 |
| Raw repository token baseline | 394,638 |

The raw token baseline is the benchmark estimator's count for all discoverable
repository files. Graph context tokens are the source-bearing context returned
for a specific task and target. The verification run had a small documentation
diff, so the meaningful comparison here is graph context against the raw
repository baseline.

## Token Reduction Results

| Scenario | Profile | Raw repo tokens | Graph context tokens | Reduction | Raw/context ratio | Returned nodes | MCP response bytes | Context call |
|----------|---------|----------------:|---------------------:|----------:|------------------:|---------------:|-------------------:|-------------:|
| Explain `ContextService.build_context` | small | 394,638 | 8,956 | 97.73% | 44.06x | 18 | 16,492 | 49.763 ms |
| Explain `ContextService.build_context` | medium | 394,638 | 12,891 | 96.73% | 30.61x | 54 | 46,763 | 54.646 ms |
| Explain `IndexService.index` | small | 394,638 | 6,564 | 98.34% | 60.12x | 18 | 15,488 | 52.200 ms |

The token-reduction numbers come from the benchmark's source-bearing context
measurement. `Returned nodes`, `MCP response bytes`, and `Context call` come
from the adjacent context retrieval step in the same benchmark run.

## Context Quality Check

The self-corpus benchmark verifies that smaller context is still finding the
files and symbols expected for known repository tasks.

| Metric | Value |
|--------|------:|
| Corpus tasks | 5 |
| Passed tasks | 5 |
| Failed tasks | 0 |
| Overall expected-hit rate | 100% |
| Task pass rate | 100% |
| Total graph context tokens | 6,127 |
| Average graph context tokens | 1,225.4 |
| Total MCP response bytes | 79,456 |
| Average MCP response bytes | 15,891.2 |
| Tool calls | 5 |

Per-task hits:

| Task | Expected hits | Missing hits | Returned nodes | Context tokens | Response bytes |
|------|--------------:|-------------:|---------------:|---------------:|---------------:|
| `context-build-context` | 8 / 8 | 0 | 18 | 1,145 | 16,491 |
| `index-pipeline` | 7 / 7 | 0 | 18 | 1,225 | 15,489 |
| `discovery-vcs-index` | 5 / 5 | 0 | 16 | 1,388 | 15,906 |
| `minimal-index-health` | 4 / 4 | 0 | 18 | 1,364 | 16,477 |
| `benchmark-pipeline` | 7 / 7 | 0 | 18 | 1,005 | 15,093 |

## What This Shows

CseGraph reduces token use by routing the agent through indexed code structure:

- A targeted `small` profile request returned 6,564 to 8,956 context tokens
  instead of the 394,638-token raw repository baseline.
- The measured reduction was 97.73% to 98.34% for the `small` profile target
  runs.
- The `medium` profile widened the retrieval from 18 nodes to 54 nodes while
  still reducing context by 96.73%.
- The corpus run kept a 100% expected-hit rate across 5 repository tasks, so
  the reduction did not come from dropping the expected files or symbols.

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
will vary with repository size, profile, target, and query specificity. The
working-tree diff during the final verification run was documentation-only, so
this report does not measure diff-only review context.
