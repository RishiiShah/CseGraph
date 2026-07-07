# Benchmark Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the full sandbox MCP benchmark across auto, small, medium, and large profiles, then publish two comparison docs: one for the four profiles and one for v1.8.0 versus the current v2 release.

**Architecture:** Use the existing native MCP cross-repo benchmark harness as the source of truth, but adapt the temporary runner only as needed to the current strict MCP schema. Persist each profile’s JSON and markdown report, then summarize the measured deltas in dedicated docs instead of mixing them into the benchmark outputs.

**Tech Stack:** Python 3.14, the bundled `env/bin/python`, the local `csegraph` package, markdown docs in `docs/`, benchmark artifacts in `benchmark_results/`.

## Global Constraints

- Preserve the current strict MCP tool schema: `csegraph_index`, `csegraph_refresh`, and `csegraph_context` accept only the fields documented in `docs/csegraph.md`.
- Keep benchmark execution local and offline.
- Do not overwrite historical benchmark artifacts; write new profile outputs alongside existing reports.
- Use the repo’s current release version for the comparison doc title and body (`2.0.0` unless the source version changes before finalization).

---

### Task 1: Validate the failure mode and fixability

**Files:**
- Inspect: `csegraph/_core/retrieval/freshness.py`
- Inspect: `csegraph/_core/index/services.py`
- Inspect: `csegraph/_core/server/app.py`
- Inspect: `/private/tmp/csegraph-current-benchmark/tools/cross_repo_benchmark.py`

**Interfaces:**
- Consumes: the current MCP refresh flow and the benchmark harness’s phase-B refresh call.
- Produces: a short written assessment of whether the lease failure is a benchmark artifact, a harness issue, or a product bug.

- [ ] **Step 1: Read the lease and refresh path**

Review the refresh ownership code and the MCP dispatch wrapper to identify why `csegraph_refresh` can report `Lease ownership was lost before commit.` when the benchmark mutates a sandbox file.

- [ ] **Step 2: Compare with the harness mutation pattern**

Trace how the benchmark mutates a file, triggers refresh, and then restores the file so it is clear whether the failure comes from the benchmark’s sequence or from the product lease model.

- [ ] **Step 3: Decide fixability**

Record whether the failure is likely fixable in the harness, requires a product code change, or is an expected limitation that should be documented instead of patched.

### Task 2: Run the four sandbox profiles

**Files:**
- Produce: `/private/tmp/csegraph-current-benchmark/benchmark_results/native_mcp_cross_repo_results.json`
- Produce: `/private/tmp/csegraph-current-benchmark/benchmark_results/native_mcp_cross_repo_results.md`
- Produce: `/private/tmp/csegraph-current-benchmark/benchmark_results/native_mcp_cross_repo_small.json`
- Produce: `/private/tmp/csegraph-current-benchmark/benchmark_results/native_mcp_cross_repo_small.md`
- Produce: `/private/tmp/csegraph-current-benchmark/benchmark_results/native_mcp_cross_repo_medium.json`
- Produce: `/private/tmp/csegraph-current-benchmark/benchmark_results/native_mcp_cross_repo_medium.md`
- Produce: `/private/tmp/csegraph-current-benchmark/benchmark_results/native_mcp_cross_repo_large.json`
- Produce: `/private/tmp/csegraph-current-benchmark/benchmark_results/native_mcp_cross_repo_large.md`

**Interfaces:**
- Consumes: the patched temporary benchmark harness and the sandbox repositories under `sandbox/`.
- Produces: one JSON and one markdown report per profile for `auto`, `small`, `medium`, and `large`.

- [ ] **Step 1: Run `auto`**

Execute the sandbox cross-repo benchmark with `CSEGRAPH_BENCH_PROFILE=auto` and capture the JSON and markdown output.

- [ ] **Step 2: Run `small`**

Execute the same benchmark with `CSEGRAPH_BENCH_PROFILE=small` and capture the JSON and markdown output.

- [ ] **Step 3: Run `medium`**

Execute the same benchmark with `CSEGRAPH_BENCH_PROFILE=medium` and capture the JSON and markdown output.

- [ ] **Step 4: Run `large`**

Execute the same benchmark with `CSEGRAPH_BENCH_PROFILE=large` and capture the JSON and markdown output.

- [ ] **Step 5: Verify the reports**

Check that every profile completed the full 1,000-query workload and note any phase-B skips or failures in the run summaries.

### Task 3: Write the four-profile comparison doc

**Files:**
- Create: `docs/benchmarks/sandbox-profile-comparison.md`
- Read: the four profile benchmark JSON and markdown files produced in Task 2

**Interfaces:**
- Consumes: per-profile benchmark summaries, repo-level metrics, and any phase-B skip notes.
- Produces: a single markdown comparison document covering `auto`, `small`, `medium`, and `large`.

- [ ] **Step 1: Draft the comparison table**

Summarize each profile’s aggregate reduction, average MCP latency, phase-B totals, and any notable failures in one table.

- [ ] **Step 2: Add interpretation**

Explain where each profile is strongest and where it degrades, with emphasis on latency versus compression tradeoffs.

- [ ] **Step 3: Add provenance**

State the benchmark command and the exact report filenames used to derive the comparison.

### Task 4: Write the v1.8.0 vs v2.0.0 comparison doc

**Files:**
- Create: `docs/benchmarks/v1.8.0-v2.0.0-comparison.md`
- Read: `docs/benchmarks.md`
- Read: `/private/tmp/csegraph-current-benchmark/benchmark_results/native_mcp_cross_repo_results.md`

**Interfaces:**
- Consumes: the historical v1.8.0 benchmark numbers and the current sandbox benchmark summary.
- Produces: a markdown document explaining how v2 compares to v1.8.0 on the same sandbox workload family.

- [ ] **Step 1: Extract the historical baseline**

Pull the published v1.8.0 numbers from the old benchmark report and keep them in the doc as the baseline.

- [ ] **Step 2: Compare against the current run**

Report the measured deltas for aggregate reduction, average latency, and phase-B totals.

- [ ] **Step 3: Call out regressions and caveats**

Explicitly mention the small-repo latency regressions and the `django` phase-B lease failure so the comparison is honest.

- [ ] **Step 4: Add a short conclusion**

State whether v2 is an overall improvement and in which workload regimes it excels or falls short.

## Self-Review

- Task coverage: all requested benchmark profiles and both requested comparison docs are covered.
- Placeholder scan: no TBD/TODO placeholders or vague “add tests” steps.
- Type consistency: file paths are consistent with the current repo layout and benchmark artifacts.
