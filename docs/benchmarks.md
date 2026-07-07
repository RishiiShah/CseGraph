# Benchmark Evidence

This page compares the checked-in `v1.8.0` baseline against the latest `2.0.0`
cross-repository rerun. The benchmark harness launches the CseGraph MCP server
as a separate stdio process and calls tools through the official
`mcp.client` JSON-RPC path.

## Method

- MCP latency is measured as the client-side round trip around
  `session.call_tool(...)`; token counting and report writing are excluded.
- The baseline reads all included source and text files once per query.
- `chars/4` counts are CseGraph's transparent heuristic and are reported
  alongside exact UTF-8 bytes and OpenAI proxy counts.
- Profile choice changes retrieval latency, not the benchmark workload.

## Current Sweep

Latest rerun: CseGraph `2.0.0`

| Profile | Total naive chars/4 | MCP chars/4 | chars/4 reduction | Total naive OpenAI proxy | MCP OpenAI proxy | OpenAI proxy reduction | Avg MCP latency | Avg Phase B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `auto` | 4,350,352,900 | 234,676 | 18,537.7x | 4,222,438,000 | 264,576 | 15,959.3x | 88.4ms | 8,934.6ms |
| `small` | 4,350,352,900 | 234,676 | 18,537.7x | 4,222,438,000 | 264,576 | 15,959.3x | 101.0ms | 9,293.7ms |
| `medium` | 4,350,352,900 | 234,676 | 18,537.7x | 4,222,438,000 | 264,576 | 15,959.3x | 101.6ms | 8,788.5ms |
| `large` | 4,350,352,900 | 234,676 | 18,537.7x | 4,222,438,000 | 264,576 | 15,959.3x | 102.9ms | 9,391.6ms |

What the sweep shows:

- `auto` is the best default latency profile.
- `medium` is the best Phase B profile.
- `small` never wins on aggregate latency or Phase B, so it is mostly a
  regression mode.
- `large` is the slowest overall.
- The token reductions are identical across all four profiles, so profile choice
  is trading latency, not retrieval breadth.

## Per-Sandbox Fixture Token Footprint

These totals are from the `auto` run, but the token counts are identical across
all four profiles. Each fixture ran 100 queries, so per-query averages are the
totals divided by 100. Reduction percentages are computed as
`1 - MCP / naive`, rounded to 3 decimals.

### chars/4 Footprint

| Repository | Queries | Total naive chars/4 | Total MCP content chars/4 | Content reduction % | Total MCP envelope chars/4 | Envelope reduction % |
|---|---:|---:|---:|---:|---:|---:|
| `nanoGPT` | 100 | 1,660,200 | 17,152 | 98.967% | 22,073 | 98.670% |
| `micrograd` | 100 | 224,000 | 18,025 | 91.953% | 23,178 | 89.653% |
| `django` | 100 | 704,652,100 | 23,835 | 99.997% | 29,439 | 99.996% |
| `pandas` | 100 | 593,543,600 | 24,143 | 99.996% | 29,802 | 99.995% |
| `flask` | 100 | 15,754,200 | 23,409 | 99.851% | 29,056 | 99.816% |
| `transformers` | 100 | 2,110,603,300 | 33,060 | 99.998% | 38,808 | 99.998% |
| `scikit-learn` | 100 | 403,429,300 | 23,835 | 99.994% | 29,317 | 99.993% |
| `fastapi` | 100 | 336,417,000 | 21,599 | 99.994% | 27,067 | 99.992% |
| `celery` | 100 | 91,927,400 | 24,319 | 99.974% | 29,760 | 99.968% |
| `pytest` | 100 | 92,141,800 | 25,299 | 99.973% | 30,976 | 99.966% |

### OpenAI Proxy Footprint

| Repository | Queries | Total naive OpenAI proxy | Total MCP content OpenAI proxy | Content reduction % | Total MCP envelope OpenAI proxy | Envelope reduction % |
|---|---:|---:|---:|---:|---:|---:|
| `nanoGPT` | 100 | 1,723,200 | 20,418 | 98.815% | 27,167 | 98.423% |
| `micrograd` | 100 | 254,100 | 22,299 | 91.224% | 29,338 | 88.454% |
| `django` | 100 | 654,951,600 | 26,094 | 99.996% | 34,116 | 99.995% |
| `pandas` | 100 | 620,289,200 | 28,024 | 99.995% | 36,069 | 99.994% |
| `flask` | 100 | 14,661,800 | 26,520 | 99.819% | 34,642 | 99.764% |
| `transformers` | 100 | 2,001,146,400 | 35,453 | 99.998% | 43,912 | 99.998% |
| `scikit-learn` | 100 | 400,175,400 | 26,649 | 99.993% | 34,436 | 99.991% |
| `fastapi` | 100 | 361,108,800 | 23,758 | 99.993% | 31,374 | 99.991% |
| `celery` | 100 | 83,120,100 | 27,375 | 99.967% | 35,070 | 99.958% |
| `pytest` | 100 | 85,007,400 | 27,986 | 99.967% | 36,177 | 99.957% |

## Per-Repository Winners

| Repository | Best latency profile | Best latency | Best Phase B profile | Best Phase B |
|---|---|---:|---|---:|
| `nanoGPT` | `auto` | 8.0ms | `medium` | 63.0ms |
| `micrograd` | `auto` | 4.8ms | `auto` | 60.8ms |
| `django` | `medium` | 137.6ms | `medium` | 80,122.6ms |
| `pandas` | `auto` | 106.2ms | `auto` | 1,081.4ms |
| `flask` | `medium` | 71.9ms | `auto` | 256.3ms |
| `transformers` | `auto` | 159.8ms | `auto` | 3,256.8ms |
| `scikit-learn` | `auto` | 104.0ms | `auto` | 528.1ms |
| `fastapi` | `auto` | 103.4ms | `auto` | 479.1ms |
| `celery` | `auto` | 88.6ms | `auto` | 1,073.5ms |
| `pytest` | `auto` | 98.7ms | `auto` | 297.8ms |

The only repositories that prefer `medium` for context latency are `django`
and `flask`. Everything else is fastest on `auto`. The remaining outlier is
`django` Phase B, which still dominates wall time even on the best profile.

## v1.8.0 vs 2.0.0

### Latency And Phase B

| Profile | v1.8.0 Avg MCP latency | 2.0.0 Avg MCP latency | Speedup | v1.8.0 Avg Phase B | 2.0.0 Avg Phase B | Speedup |
|---|---:|---:|---:|---:|---:|---:|
| `auto` | 1,118.4ms | 88.4ms | 12.6x | 15,748.2ms | 8,934.6ms | 1.8x |
| `small` | 1,141.3ms | 101.0ms | 11.3x | 16,378.6ms | 9,293.7ms | 1.8x |
| `medium` | 1,120.3ms | 101.6ms | 11.0x | 15,863.4ms | 8,788.5ms | 1.8x |
| `large` | 1,142.4ms | 102.9ms | 11.1x | 15,935.8ms | 9,391.6ms | 1.7x |

### Token Reduction

| Profile | v1.8.0 chars/4 reduction | 2.0.0 chars/4 reduction | v1.8.0 OpenAI proxy reduction | 2.0.0 OpenAI proxy reduction |
|---|---:|---:|---:|---:|
| `auto` | 299.8x | 18,537.7x | 280.7x | 15,959.3x |
| `small` | 246.4x | 18,537.7x | 231.3x | 15,959.3x |
| `medium` | 246.9x | 18,537.7x | 232.1x | 15,959.3x |
| `large` | 243.0x | 18,537.7x | 227.6x | 15,959.3x |

The practical readout is straightforward:

- `2.0.0` is materially faster across every profile.
- `auto` gives the best latency without sacrificing token efficiency.
- `medium` is the best compromise when Phase B wall time matters more than the
  first context round trip.
- The tiny repos are already in single-digit millisecond territory on `auto`;
  further gains there need freshness or filesystem-path reductions, not a
  different retrieval profile.
