# Agent Context Benchmarks

## CseGraph 2.0 adaptive methodology

The release baseline is no longer a full-corpus read. The deterministic
comparison uses `rg --json`, exact-name/path ranking, bounded 80-line selective
reads, one-hop import following, and the same exact `o200k_base` response budget
as CseGraph. Pull-request CI runs the 20-task pinned corpus in
`benchmarks/adaptive/pr_tasks.json`; nightly and release jobs expand the same
schema with pinned cross-repository and agent patch/test trials.

```bash
env/bin/python tools/run_adaptive_retrieval_benchmark.py \
  --corpus benchmarks/adaptive/pr_tasks.json \
  --output benchmark_results/adaptive_retrieval.json
```

Release gates cover exact budget compliance, target resolution, required-slice
recall and precision, median tokens, p95 latency, tool calls, cache behavior,
and stale-context failures. Historical full-corpus comparisons remain below
for reproducibility only and are not used as evidence for 2.0 product claims.

## Native MCP Cross-Repo Results (CseGraph 1.8.0)

| Profile | Naive chars/4 tokens | MCP chars/4 tokens | chars/4 reduction | Naive OpenAI proxy tokens | MCP OpenAI proxy tokens | OpenAI proxy reduction | Avg MCP latency | Avg Phase B |
|---------|---------------------:|-------------------:|------------------:|--------------------------:|------------------------:|-----------------------:|----------------:|------------:|
| `auto` | 4,328,147,800 | 14,438,662 | 299.8x | 4,201,341,600 | 14,967,049 | 280.7x | 1,118.4 ms | 15,748.2 ms |
| `small` | 4,328,147,800 | 17,567,138 | 246.4x | 4,201,341,600 | 18,167,571 | 231.3x | 1,141.3 ms | 16,378.6 ms |
| `medium` | 4,328,147,800 | 17,533,426 | 246.9x | 4,201,341,600 | 18,100,920 | 232.1x | 1,120.3 ms | 15,863.4 ms |
| `large` | 4,328,147,800 | 17,813,499 | 243.0x | 4,201,341,600 | 18,457,154 | 227.6x | 1,142.4 ms | 15,935.8 ms |

## Auto Profile

| Repository | Source/text files | Queries | MCP chars/4 tokens | chars/4 reduction | OpenAI proxy reduction | Avg MCP latency | P95 MCP latency | Phase B total |
|------------|------------------:|--------:|-------------------:|------------------:|-----------------------:|----------------:|----------------:|--------------:|
| `nanoGPT` | 19 | 100 / 100 | 304,577 | 5.5x | 5.2x | 7.1 ms | 10.8 ms | 91.1 ms |
| `micrograd` | 6 | 100 / 100 | 347,302 | 0.6x | 0.7x | 5.5 ms | 6.9 ms | 91.5 ms |
| `django` | 4,208 | 100 / 100 | 2,053,927 | 342.9x | 311.3x | 2,465.8 ms | 3,071.9 ms | 26,745.7 ms |
| `pandas` | 1,741 | 100 / 100 | 3,860,835 | 153.0x | 153.8x | 2,505.0 ms | 2,969.7 ms | 11,749.1 ms |
| `flask` | 136 | 100 / 100 | 836,426 | 18.8x | 17.2x | 54.9 ms | 92.0 ms | 552.6 ms |
| `transformers` | 6,049 | 100 / 100 | 1,708,070 | 1,225.3x | 1,119.3x | 4,256.9 ms | 5,414.6 ms | 106,987.2 ms |
| `scikit-learn` | 1,183 | 100 / 100 | 2,362,909 | 170.6x | 162.3x | 843.2 ms | 1,032.8 ms | 4,338.9 ms |
| `fastapi` | 2,740 | 100 / 100 | 1,557,009 | 215.9x | 228.5x | 264.9 ms | 317.0 ms | 2,305.3 ms |
| `celery` | 525 | 100 / 100 | 739,369 | 123.9x | 105.8x | 413.0 ms | 478.8 ms | 2,673.3 ms |
| `pytest` | 302 | 100 / 100 | 668,238 | 137.1x | 122.2x | 368.0 ms | 440.5 ms | 1,947.5 ms |

## Small Profile

| Repository | Source/text files | Queries | MCP chars/4 tokens | chars/4 reduction | OpenAI proxy reduction | Avg MCP latency | P95 MCP latency | Phase B total |
|------------|------------------:|--------:|-------------------:|------------------:|-----------------------:|----------------:|----------------:|--------------:|
| `nanoGPT` | 19 | 100 / 100 | 313,347 | 5.3x | 5.1x | 6.3 ms | 9.5 ms | 86.5 ms |
| `micrograd` | 6 | 100 / 100 | 384,550 | 0.6x | 0.6x | 5.0 ms | 6.1 ms | 86.7 ms |
| `django` | 4,208 | 100 / 100 | 2,107,882 | 334.1x | 304.3x | 2,410.8 ms | 3,048.9 ms | 25,751.5 ms |
| `pandas` | 1,741 | 100 / 100 | 3,561,403 | 165.9x | 166.1x | 2,488.3 ms | 3,006.0 ms | 11,949.9 ms |
| `flask` | 136 | 100 / 100 | 1,534,528 | 10.3x | 9.3x | 60.2 ms | 101.4 ms | 583.4 ms |
| `transformers` | 6,049 | 100 / 100 | 1,730,603 | 1,209.3x | 1,106.3x | 4,506.8 ms | 5,903.2 ms | 113,956.6 ms |
| `scikit-learn` | 1,183 | 100 / 100 | 2,414,588 | 167.0x | 160.5x | 856.6 ms | 1,042.8 ms | 4,304.2 ms |
| `fastapi` | 2,740 | 100 / 100 | 1,597,271 | 210.5x | 222.8x | 272.7 ms | 329.9 ms | 2,368.0 ms |
| `celery` | 525 | 100 / 100 | 2,060,954 | 44.4x | 38.6x | 427.1 ms | 502.9 ms | 2,728.1 ms |
| `pytest` | 302 | 100 / 100 | 1,862,012 | 49.2x | 43.9x | 379.1 ms | 456.6 ms | 1,971.4 ms |

## Medium Profile

| Repository | Source/text files | Queries | MCP chars/4 tokens | chars/4 reduction | OpenAI proxy reduction | Avg MCP latency | P95 MCP latency | Phase B total |
|------------|------------------:|--------:|-------------------:|------------------:|-----------------------:|----------------:|----------------:|--------------:|
| `nanoGPT` | 19 | 100 / 100 | 317,274 | 5.2x | 5.0x | 6.6 ms | 10.1 ms | 116.3 ms |
| `micrograd` | 6 | 100 / 100 | 383,661 | 0.6x | 0.6x | 5.4 ms | 6.2 ms | 91.3 ms |
| `django` | 4,208 | 100 / 100 | 2,344,230 | 300.4x | 276.8x | 2,416.6 ms | 2,960.6 ms | 25,890.1 ms |
| `pandas` | 1,741 | 100 / 100 | 3,190,068 | 185.2x | 185.3x | 2,452.8 ms | 2,918.9 ms | 11,917.5 ms |
| `flask` | 136 | 100 / 100 | 1,542,168 | 10.2x | 9.3x | 57.7 ms | 96.7 ms | 560.3 ms |
| `transformers` | 6,049 | 100 / 100 | 1,751,306 | 1,195.0x | 1,096.8x | 4,326.0 ms | 5,545.3 ms | 108,637.6 ms |
| `scikit-learn` | 1,183 | 100 / 100 | 2,420,315 | 166.6x | 159.7x | 854.5 ms | 1,042.3 ms | 4,332.5 ms |
| `fastapi` | 2,740 | 100 / 100 | 1,567,169 | 214.5x | 226.9x | 272.2 ms | 327.6 ms | 2,350.6 ms |
| `celery` | 525 | 100 / 100 | 2,126,502 | 43.1x | 37.5x | 422.6 ms | 487.2 ms | 2,696.0 ms |
| `pytest` | 302 | 100 / 100 | 1,890,733 | 48.5x | 43.1x | 389.1 ms | 493.4 ms | 2,041.6 ms |

## Large Profile

| Repository | Source/text files | Queries | MCP chars/4 tokens | chars/4 reduction | OpenAI proxy reduction | Avg MCP latency | P95 MCP latency | Phase B total |
|------------|------------------:|--------:|-------------------:|------------------:|-----------------------:|----------------:|----------------:|--------------:|
| `nanoGPT` | 19 | 100 / 100 | 318,239 | 5.2x | 5.0x | 7.1 ms | 10.8 ms | 91.4 ms |
| `micrograd` | 6 | 100 / 100 | 384,187 | 0.6x | 0.6x | 5.3 ms | 6.1 ms | 91.4 ms |
| `django` | 4,208 | 100 / 100 | 2,000,896 | 352.0x | 319.7x | 2,432.1 ms | 3,046.1 ms | 26,522.5 ms |
| `pandas` | 1,741 | 100 / 100 | 3,520,870 | 167.8x | 168.4x | 2,588.7 ms | 3,294.3 ms | 12,248.3 ms |
| `flask` | 136 | 100 / 100 | 1,542,408 | 10.2x | 9.3x | 57.9 ms | 94.6 ms | 568.8 ms |
| `transformers` | 6,049 | 100 / 100 | 1,753,042 | 1,193.9x | 1,094.5x | 4,396.3 ms | 5,669.2 ms | 108,458.7 ms |
| `scikit-learn` | 1,183 | 100 / 100 | 2,429,158 | 166.0x | 159.2x | 858.8 ms | 1,045.9 ms | 4,355.3 ms |
| `fastapi` | 2,740 | 100 / 100 | 1,565,254 | 214.8x | 227.2x | 269.3 ms | 322.8 ms | 2,325.8 ms |
| `celery` | 525 | 100 / 100 | 2,098,838 | 43.6x | 37.5x | 427.2 ms | 497.0 ms | 2,722.3 ms |
| `pytest` | 302 | 100 / 100 | 2,200,607 | 41.6x | 37.0x | 381.7 ms | 451.2 ms | 1,973.7 ms |
