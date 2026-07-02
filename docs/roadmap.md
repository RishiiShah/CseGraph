# CseGraph Product Vision & Roadmap

## The Core Principle
**CseGraph should degrade gracefully into an excellent indexed search tool on easy tasks, then reveal its graph advantage only when the task becomes structurally difficult.**

The current iteration of CseGraph exposes too much machinery by default, paying the full graph overhead (latency and token bloat) even for trivial tasks on small repositories. The next generation of CseGraph must shift from being specialized graph infrastructure to an adaptive search engine that competes with a strong, reproducible `rg` plus selective-read workflow across all scales.

## Strategic Roadmap

| Area | Current Behavior | Target Behavior | Success Criterion |
|---|---|---|---|
| **Primary Competitor** | Naive full-repository reading | Strong `rg` + selective reads + LSP baseline | Beats realistic agent workflows |
| **Easy Queries** | Pays full graph and formatting overhead | Use fast lexical/symbol lookup | Similar latency and tokens to `rg` |
| **Tool Workflow** | Multiple mandatory routing/context calls | One call for ordinary tasks; progressive escalation | Most tasks resolved in one call |
| **`csegraph_minimal`** | Returns a routing card | Return the useful code slice directly | Immediately actionable output |
| **Graph Usage** | Graph structure appears by default | Use graph internally for ranking | Users pay only for relevant code |
| **Graph Expansion** | Broad/static expansion | Expand only when ambiguity or dependencies justify it | Fewer irrelevant symbols |
| **Output Format** | Verbose structural Markdown | Compact paths, line ranges, snippets, and relevance reasons | Minimal formatting overhead |
| **Token Control** | Output size determined implicitly | Add strict token/snippet budgets | Typical retrieval under 300–800 tokens |
| **Duplicate Context** | Information can recur across calls | Deduplicate previously returned symbols and snippets | Lower cumulative task tokens |
| **Ambiguous Symbols** | Metadata-heavy context | Rank using scope, imports, references, and graph proximity | Correct target ranks first |
| **Structural Questions**| Mixed into normal retrieval | Reserve graph/path output for explicit structural requests | No graph tax on simple edits |
| **Index Freshness** | Staleness can interrupt the workflow | Incremental refresh with explicit freshness metadata | Near-zero stale retrieval failures |
| **Multi-Agent Use** | Agents independently request overlapping context | Shared index and reusable retrieval cache | Reduced aggregate retrieval cost |
| **Benchmark Unit** | Retrieval output measured in isolation | Measure complete coding tasks | Comparable end-to-end evidence |
| **Benchmark Metrics** | Tokens, latency, and CseGraph accuracy | Total tokens, wall time, tool calls, success rate, edit correctness, refresh cost | Clear performance frontier |
| **Product Positioning** | Specialized graph infrastructure | Adaptive search that becomes graph-aware only when useful | Valuable on small, medium, and large repositories |
