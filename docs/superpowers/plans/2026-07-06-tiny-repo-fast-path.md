# Tiny Repo Fast Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce latency on tiny repositories by bypassing full candidate discovery when the target can be resolved directly, without changing the public context contract.

**Architecture:** Add a narrow size-based branch inside the adaptive context service. The branch uses metadata-backed repo counts to detect tiny repos, then uses a direct target-resolution fast path for exact tiny targets while preserving the same response schema and larger-repo behavior. Keep the change local to retrieval, metadata publication, and one regression test so the behavior stays easy to reason about.

**Tech Stack:** Python 3.14, SQLite, pytest, current CseGraph adaptive retrieval code.

## Global Constraints

- Preserve the `csegraph-context-v5` response schema.
- Keep the change local to adaptive retrieval and its tests.
- Do not weaken correctness for non-tiny repositories.
- Publish repo size counts into index metadata during indexing and refresh so the retrieval path can classify tiny repos without re-counting files and symbols on every request.

---

### Task 1: Add a tiny-repo branch to adaptive retrieval

**Files:**
- Modify: `csegraph/_core/index/repository.py`
- Modify: `csegraph/_core/index/services.py`
- Modify: `csegraph/_core/retrieval/adaptive.py`

**Interfaces:**
- Consumes: `ProjectIndex`, the current request task/target, and repository size counts.
- Produces: the same `ContextResponse`, but with less work performed for tiny repositories.

- [ ] **Step 1: Add a failing test**

Write a regression test that exercises a tiny repository and asserts the response still returns `ContextStatus.READY`, but the aggressive tiny path does not call full candidate discovery when the target is an exact tiny match.

- [ ] **Step 2: Run the test to verify it fails**

Run: `env/bin/python -m pytest tests/integration/test_adaptive_context.py -k tiny_repo_exact_target_skips_full_candidate_discovery -v`
Expected: the new test fails because the tiny-repo branch does not exist yet.

- [ ] **Step 3: Implement the metadata-backed tiny fast path**

```python
def _is_tiny_repo(metadata: dict[str, str]) -> bool:
    file_count = metadata.get("file_count")
    symbol_count = metadata.get("symbol_count")
    return bool(file_count and symbol_count) and int(file_count) <= 100 and int(symbol_count) <= 1000
```

Use the branch in `ContextService.retrieve` to resolve exact tiny targets directly from the index, and fall back to the normal discovery path when the exact lookup is ambiguous.

- [ ] **Step 4: Run the test to verify it passes**

Run: `env/bin/python -m pytest tests/integration/test_adaptive_context.py -k tiny_repo_exact_target_skips_full_candidate_discovery -v`
Expected: PASS.

### Task 2: Verify small-repo behavior with focused tests

**Files:**
- Modify: `tests/integration/test_adaptive_context.py`

**Interfaces:**
- Consumes: the public `ContextService` behavior.
- Produces: a regression test that protects the aggressive tiny fast path from regressing back to full candidate discovery.

- [ ] **Step 1: Add a discovery-bypass assertion**

Assert that a tiny repository still returns a useful target with a compact response while `_discover_candidates` is never called for the exact tiny target case.

- [ ] **Step 2: Run the focused test module**

Run: `env/bin/python -m pytest tests/integration/test_adaptive_context.py tests/integration/test_context_contract.py tests/integration/test_impact_context.py tests/integration/test_typescript_indexing.py -q`
Expected: pass.

### Task 3: Re-run the targeted benchmark slice

**Files:**
- Produce: new local benchmark output files under `/private/tmp/csegraph-current-benchmark/benchmark_results/`

**Interfaces:**
- Consumes: the updated retrieval code.
- Produces: updated benchmark evidence for small-repo latency.

- [ ] **Step 1: Run the sandbox benchmark for the tiny repos**

Measure the effect on `nanoGPT` and `micrograd` first, since those are the repos that were regressing in the previous `auto` run.

- [ ] **Step 2: Decide whether profile tuning is still warranted**

If the aggressive tiny path improves latency enough, keep it. If it regresses behavior, revert and move to the next fallback path.

### Task 4: Commit the change

**Files:**
- Modify: the files changed in Tasks 1-3

**Interfaces:**
- Consumes: the verified retrieval change and regression test.
- Produces: a local commit with the tiny-repo optimization.

- [ ] **Step 1: Review the diff**

Inspect the code and test diff for scope creep.

- [ ] **Step 2: Commit**

```bash
git add csegraph/_core/index/repository.py csegraph/_core/index/services.py csegraph/_core/retrieval/adaptive.py csegraph/_core/retrieval/adaptive_discovery.py csegraph/_core/retrieval/adaptive_constants.py tests/integration/test_adaptive_context.py docs/superpowers/plans/2026-07-06-tiny-repo-fast-path.md
git commit -m "Optimize adaptive retrieval for tiny repos"
```
