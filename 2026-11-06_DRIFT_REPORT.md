# CseGraph working-diff review

Status: resolved. Six code findings and three documentation findings repaired.

What this change does: refresh returns bounded context for changed symbols and
their dependents. Retrieval follows two-hop relationships, and prose compression
reduces output and storage text.

Scope: uncommitted refresh, retrieval, compression, CLI/MCP, and documentation changes.
Assumed load: local repositories with concurrent CLI/MCP refresh callers.
Existing uncommitted changes are preserved.
Review method: Ponytail source/caller review and focused EGM drift review.
This is not the EGM five-stage design-review pipeline or a converged review loop.

## Reviewer evidence

- `egm-codebase-details`: completed in fresh conversation context (`fork_turns: none`).
  Confirmed reading installed persona, Codex integration, re-implementation standard,
  drift skill, and all six target documents.
- `egm-design`: completed in fresh conversation context (`fork_turns: none`).
  Confirmed reading installed persona, Codex integration, re-implementation standard,
  target architecture, five supporting documents, diff, and relevant source.
- `egm-codebase-details` verification: completed in fresh conversation context
  (`fork_turns: none`). Confirmed persona, Codex integration, standard, all six
  documentation targets, source, diff, and caller/test reads. Reported two further
  findings; both reproduced and repaired below.
- Instruction-file exclusion was not probed; fresh conversation context does not
  establish filesystem or host-instruction isolation.

## docs/architecture.md

### Must fix — repaired

#### 1. codebase-1 — Blocker — disappearing path names

- Scope: `csegraph/_core/graph/queries.py:200` and `csegraph/_core/core/serializer.py:82`.
- **What this is:** path queries display the names along a graph path.
- **Problem:** successful path summaries compressed bare symbol names, contradicting
  the identifier-preservation contract. `a → the (1 hops)` becomes `→ (1 hops)`.
- AI suggestion: PROPOSE FIX.
- **Fix:** preserve already-terse path summaries verbatim in construction and serialization.
- **If skipped:** the displayed path loses valid symbol names.
- Evidence: real graph-service/serializer regression fails before repair.
- Also reported independently by `egm-design` as Major.

#### 2. codebase-2 — Blocker — lost context after partial symbol removal

- Scope: `csegraph/_core/index/services.py:400` and
  `csegraph/_core/retrieval/freshness/coordinator.py:73`.
- **What this is:** refresh returns source slices for changed and affected symbols.
- **Problem:** deleting or renaming `c` while retaining disconnected `unrelated`
  in `c.py` leaves `a.py` and `b.py` in `impacted_files`, but omits their slices.
  Their former call edges disappear, and the remaining seed cannot reach them.
- AI suggestion: PROPOSE FIX.
- **Fix:** seed unreachable symbols from captured dependent files after ordinary
  changed-symbol expansion in both direct and reused responses. Use the existing
  published file lists; no extra metadata or removed-symbol flag is needed.
- **If skipped:** callers affected by a removed symbol disappear from pushed context.
- Evidence: four regressions fail for deletion/rename and direct/reused results.
- Also reported independently by `egm-design` as Major.

#### 3. ponytail-1 — Major — reversed decorator roles

- Scope: `csegraph/_core/retrieval/impact_context.py`, `build_impact_context`.
- **What this is:** context labels each selected symbol's relationship to changed code.
- **Problem:** pushed context treated the decorated function as a dependency of its
  decorator and the decorator as a caller of the decorated function.
  Ordinary retrieval already handles the edge direction correctly.
- AI suggestion: PROPOSE FIX.
- **Fix:** reuse `_relationship_role` for direct roles; retain cascade roles at later hops.
  Reuse file-symbol bridging too, removing its duplicate implementation.
- **If skipped:** context labels decorators and decorated functions backwards.
- Evidence: two real refresh regressions fail, one for each changed endpoint.

#### 4. design-1 — Major — relationship cap applied per node

- Scope: `csegraph/_core/retrieval/adaptive_expansion.py:63`.
- **What this is:** context bounds graph expansion with a relationship allowance.
- **Problem:** each frontier node received the full relationship allowance;
  accumulated relationships can exceed the advertised retrieval cap.
- AI suggestion: PROPOSE FIX.
- **Fix:** stop traversal when the shared relationship allowance is exhausted.
- **If skipped:** dense graphs multiply discovery work and can force diagnostics out
  of the response budget.
- Evidence: regression returned 72 relationships against an allowance of 16;
  it now stays within the allowance and still reaches the second hop.

#### 5. verification-1 — Major — missing import-only dependent context

- Scope: `csegraph/_core/index/services.py:400`,
  `csegraph/_core/retrieval/freshness/coordinator.py:74`, and
  `csegraph/_core/retrieval/impact_context.py`, `build_impact_context`.
- **What this is:** dependent refresh discovers file-level import chains as well
  as symbol calls.
- **Problem:** independent functions in import-dependent files appeared in
  `impacted_files` but disappeared from context because symbol seeds could not
  reach file-level import edges.
- AI suggestion: PROPOSE FIX.
- **Fix:** use the fallback seed policy from finding 2 for every captured
  dependent. Expand ordinary changed-symbol relationships first to preserve
  existing caller/dependency roles and cascade distances.
- **If skipped:** a reported impacted file can have no context despite ample budget.
- Evidence: direct and reused refresh regressions both failed before repair.

### Should fix — repaired

#### 6. verification-2 — Nit — false dependent overflow

- Scope: `csegraph/_core/index/services.py:466`, `_cascade_dependent_files`.
- **What this is:** refresh reports when its dependent-file allowance omits work.
- **Problem:** exactly two dependents with a limit of two reported overflow,
  even when no further dependent existed.
- AI suggestion: PROPOSE FIX.
- **Fix:** probe for one additional unvisited dependent before setting overflow.
- **If skipped:** callers cannot distinguish a complete refresh from truncated work.
- Evidence: exact-limit regression failed; overflow control already passed.

### Documentation findings — repaired

- `design-2` — Major — PROPOSE FIX: architecture omitted refresh defaults/range,
  `impacted_files` union semantics, context roles, traversal limits, and reuse
  counters. Added a compact description of those observable contracts.
- `design-3` — Major — PROPOSE FIX: architecture promised whole-symbol source on
  ordinary retrieval despite default target-only source and first-line impact
  summaries. Documented `auto`, `always`, and `never` behavior separately from refresh.
- `design-4` — Nit — PROPOSE FIX: runtime dependency list omitted `tiktoken`.
  Added the installed runtime dependency to that list.

## README.md

- Same dependent-context discrepancies as codebase-2 and verification-1; repaired.

## CHANGELOG.md

- Identifier-preservation claim was contradicted by codebase-1; repaired.

## docs/standards/ATSE-1000.md

- Byte-preservation rule was contradicted by codebase-1; repaired.
- Updated implementation notes to describe preserving already-terse path summaries
  verbatim after repair.

## docs/standards/ATE-1000.md

No drift detected within changed implementation scope.

## docs/benchmarks.md

No drift detected within changed implementation scope.
PR/nightly benchmarks were reproduced after repairs; documented reductions remain
65.93% and 63.72% respectively.

## Verification

- Baseline full suite: 412 passed, 3 skipped.
- Added 12 regression cases: 11 reproduced defects before repair; one exercised
  the existing overflow control.
- Final focused refresh/retrieval/MCP/graph/indexing suite: 65 passed.
- Final full suite: 424 passed, 3 skipped.
- Ruff lint/format, mypy, and `git diff --check`: passed.
- PR: 22 tasks; all configured gates pass.
- Nightly: 60 tasks; all configured gates pass.
- Both runs: cold indexes, Pyright off; 100% recall, precision, and role recall;
  all responses within token budgets.
- Reports: `/tmp/csegraph-pr-review.json`, `/tmp/csegraph-nightly-review.json`.
- No reported findings remain unaddressed.
- Existing changes are preserved; no commits or pushes were made.

Verdict: Ship.

## Limits

Full five-stage EGM review, dependency alerts, release benchmarks, and sandbox
benchmarks are outside this focused review.
Changed/dependent seed sets can exceed the pushed-context expansion ceiling;
large seed-set performance was not benchmarked. This limit is documented in the
architecture. Benchmark gates measure retrieval evidence, not LLM coding success.
The final two findings were checked with regressions and parent source review;
no subsequent fresh review round ran.
