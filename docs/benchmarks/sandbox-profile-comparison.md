# Sandbox Profile Comparison (CseGraph 2.0.0)

This report compares the four sandbox benchmark profiles against the same 10
repositories:

- `nanoGPT`
- `micrograd`
- `django`
- `pandas`
- `flask`
- `transformers`
- `scikit-learn`
- `fastapi`
- `celery`
- `pytest`

All four profiles completed 1,000 / 1,000 queries. The aggregate token
reduction is identical across the four profiles in this build, so the
meaningful difference is runtime behavior.

The `django` lease bug is fixed in this run. Every profile now completes Phase
B successfully, so the benchmark is measuring a real refresh rather than a
skip.

## Summary

| Profile | Aggregate chars/4 reduction | Aggregate OpenAI proxy reduction | Avg MCP latency | Avg Phase B total | Notes |
|---------|----------------------------:|---------------------------------:|----------------:|------------------:|-------|
| `auto` | 18,537.7x | 15,959.3x | 139.4 ms | 9,724.4 ms | No repo wins |
| `small` | 18,537.7x | 15,959.3x | 119.9 ms | 9,332.9 ms | Best on several small-to-medium repos |
| `medium` | 18,537.7x | 15,959.3x | 120.8 ms | 8,841.9 ms | Best Phase B average across the suite |
| `large` | 18,537.7x | 15,959.3x | 118.8 ms | 8,770.2 ms | Best overall average runtime |

## Where Each Profile Wins

| Repository | Best latency profile | Best Phase B profile | Notes |
|------------|---------------------|---------------------|-------|
| `nanoGPT` | `medium` | `medium` | `medium` is best on the tiny metadata-heavy workload |
| `micrograd` | `large` | `large` | `large` is fastest on both metrics |
| `django` | `large` | `large` | Lease renewal now holds and `large` is fastest end to end |
| `pandas` | `medium` | `medium` | `medium` wins the first large Python repo |
| `flask` | `medium` | `medium` | `medium` edges out `large` by a hair |
| `transformers` | `small` | `large` | `small` is best for query latency; `large` wins the refresh path |
| `scikit-learn` | `small` | `small` | `small` stays ahead on both metrics |
| `fastapi` | `small` | `small` | `small` keeps the lowest runtime cost |
| `celery` | `small` | `medium` | `small` is best on latency; `medium` is best on Phase B |
| `pytest` | `medium` | `medium` | `medium` wins both metrics |

## Takeaways

- `large` is the best average runtime choice in this build, but the margin over
  `small` and `medium` is narrow.
- `medium` wins the most Phase B repo slots, while `small` and `medium` split
  latency wins evenly.
- `auto` is competitive, but it does not win any repository in this run.
- The lease fix matters: `django` now completes in every profile, which makes
  the benchmark more useful and more representative.

## Fix Status

The `django` failure is already fixed in code. The remaining differences here
are performance tradeoffs, not correctness failures.
