# Benchmarks

CseGraph is tested on real repository questions: find a symbol, trace its
callers or dependencies, and identify relevant tests. The comparison is a
deterministic repository agent that searches and reads source files for the
same request. It does not receive the expected answer.

Latest run: 2026-07-11, cold indexes, Pyright disabled.

## Sandbox results

The sandbox contains 12 pinned public repositories, from a 5-file teaching
project to Transformers. It ran 364 source-driven tasks. The table uses the
code that was actually indexed during the run; token figures are medians per
task.

Every sandbox repository reached 100% target/evidence recall, 100% precision,
and 100% role recall for this corpus. “Context reduction” compares the median
context selected by CseGraph with the comparison agent for the same tasks.
Higher is less context sent to an agent.

| Repository | Files | Symbols | Tasks | CseGraph tokens | Comparison tokens | Context reduction |
|---|---:|---:|---:|---:|---:|---:|
| micrograd | 5 | 35 | 12 | 212.0 | 1,053.0 | 79.87% |
| nanoGPT | 15 | 30 | 12 | 194.5 | 1,027.0 | 81.06% |
| requests | 37 | 716 | 20 | 158.0 | 1,075.0 | 85.30% |
| click | 76 | 1,283 | 20 | 303.0 | 1,118.0 | 72.90% |
| Flask | 83 | 925 | 30 | 313.5 | 1,353.0 | 76.83% |
| pytest | 270 | 6,061 | 30 | 284.0 | 1,529.5 | 81.43% |
| Celery | 416 | 7,828 | 30 | 222.0 | 1,545.0 | 85.63% |
| scikit-learn | 1,022 | 11,845 | 45 | 375.0 | 2,048.0 | 81.69% |
| FastAPI | 1,133 | 5,201 | 30 | 237.5 | 1,384.0 | 82.84% |
| pandas | 1,510 | 32,180 | 45 | 255.0 | 1,759.0 | 85.50% |
| Django | 3,037 | 38,224 | 45 | 320.0 | 1,839.0 | 82.60% |
| Transformers | 4,641 | 73,412 | 45 | 304.0 | 1,910.0 | 84.08% |
| **All repositories** | **12,245** | **177,740** | **364** | **272.5** | **1,539.5** | **82.30%** |

The aggregate line is a median across all sandbox tasks. It should not be read
as a promise that every task uses 82.30% less context: the per-repository rows
show the actual range in this run.

## Release checks

These smaller suites catch regressions before the sandbox run. All configured
gates passed in the latest run.

| Suite | Tasks | Recall | Precision | Role recall | Context reduction |
|---|---:|---:|---:|---:|---:|
| PR | 22 | 100% | 100% | 100% | 67.31% |
| Nightly | 60 | 100% | 100% | 100% | 63.72% |
| Release | 30 | 100% | 100% | 100% | 65.67% |

## What this does and does not show

The results show that CseGraph returned the expected source context for the
listed tasks and revisions while keeping the context smaller than the
comparison trace. They do not measure how well a particular language model
implements a change, developer productivity, or behavior on repositories and
requests that are not in these corpora.

The task definitions, expected evidence, and repository revisions are versioned
in the repository. The comparison trace sees the request and visible context;
expected targets and evidence are kept only for scoring. Reports include each
task result, so failures are not silently removed from the totals.

## Reproduce the sandbox run

```bash
python tools/bootstrap_benchmark_sandbox.py
python tools/run_adaptive_retrieval_benchmark.py \
  --corpus sandbox --modes cold --pyright off \
  --output /tmp/csegraph-sandbox-agent.json --fail-on-gates
```

The command writes a JSON report with the per-task and per-repository values.
Use `--corpus pr`, `nightly`, or `release` for the other suites.
