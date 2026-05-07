# csegraph

csegraph is a repository context engine for coding agents.

It indexes Python code into a dependency graph and returns compact, task-specific context bundles so agents like Codex, Claude Code, Cursor, Aider, or custom agent loops do not have to repeatedly grep, cat, and scan the repo.

The goal is simple:

- fewer prompt tokens
- fewer tool calls
- less irrelevant context
- same or better edit accuracy

> **csegraph helps coding agents spend tokens on solving the task, not rediscovering the repository.**

## Why It Exists

Stop making coding agents rediscover your repo with `grep`, `cat`, and `find`. Give them a dependency graph and sufficient context up front.

```text
developer asks agent to make a change
        |
        v
agent asks csegraph for relevant context
        |
        v
csegraph returns a dependency-aware context bundle
        |
        v
agent edits with less searching, less guessing, and fewer tokens
```

## What csegraph Optimizes For

| Goal | What csegraph does |
|---|---|
| Fewer tokens | Returns compact, relevant context instead of whole files. |
| Fewer tool calls | Answers "what files/symbols matter?" directly. |
| Same or better accuracy | Includes imports, callers, callees, tests, inheritance, and decorators. |
| Better agent behavior | Explains why each node/file was included. |
| Less hallucination | Preserves exact raw code for imports, signatures, small helpers, and risky files. |

## Product Shape

```text
csegraph-core
   |-- CLI for external agents
   |     csegraph context "fix auth bug"
   |
   |-- SDK for deeper integrations
   |     ContextService.get_context(...)
   |
   `-- codegen demo / optional adapter
         not the main product
```

The primary product surface is `context`: build an index once, refresh changed files, and ask for graph-backed context before an agent edits.

## Package Layout

v1.2.3 uses four installable packages:

| Package | Location | Purpose |
|---|---|---|
| `csegraph-core` | repo root | Source-of-truth parser, SQLite index, graph traversal, retrieval, CSE metrics. Imported as `csegraph_core`. |
| `csegraph-cli` | `packages/csegraph-cli/` | Thin command-line surface for agent tools. Depends only on `csegraph-core`. |
| `csegraph` | `packages/csegraph/` | SDK facade for context retrieval. Depends on `csegraph-core`. |
| `csegraph-codegen` | `packages/csegraph-codegen/` | Optional LLM-powered code generation add-on. Imported as `csegraph_codegen`. |

Python imports use underscores, not distribution hyphens: install `csegraph-core`, import `csegraph_core`.

## Install From Source

```bash
# Core only: parser, index, retrieval, graph services.
env/bin/pip install -e .

# SDK facade: programmatic context API.
env/bin/pip install -e packages/csegraph/

# CLI: exposes the `csegraph` shell command.
env/bin/pip install -e packages/csegraph-cli/

# Optional codegen add-on.
env/bin/pip install -e packages/csegraph-codegen/

# Full development install.
env/bin/pip install -e . -e packages/csegraph/ -e packages/csegraph-cli/ -e packages/csegraph-codegen/
```

## Simple Commands

```bash
# Build the SQLite graph index once.
csegraph index .

# Refresh only changed/deleted Python files.
csegraph refresh .

# Ask for context before an agent edits.
csegraph context "fix auth token refresh bug" --target refresh_token --repo .

# Explain why a symbol/file matters.
csegraph graph refresh_token --repo . --depth 1
```

By default, the index is stored at:

```text
<repo>/.csegraph/index.db
```

Use `--profile small|medium|large` to trade retrieval breadth against speed and token budget.

## JSON-First Agent Surface

Every primary command supports JSON output for agent tools:

```bash
csegraph index . --json
csegraph refresh . --json
csegraph context "update payment retry behavior" --target PaymentClient --repo . --json
csegraph graph PaymentClient --repo . --depth 2 --json
```

A context result includes:

- ranked files and symbols
- line ranges and source paths
- dependency-aware evidence for why each node was selected
- sufficiency metrics such as dependency completeness and entity coverage
- raw-code fallbacks for exact imports, signatures, small helpers, and risky context

## SDK Usage

```python
from csegraph import ContextService, GraphQueryService, IndexService, RefreshService

IndexService(".csegraph/index.db").index(".", profile="medium")
RefreshService(".csegraph/index.db").refresh(profile="medium")

context = ContextService(".csegraph/index.db").build_context(
    task="fix auth token refresh bug",
    target="refresh_token",
    profile="medium",
)

graph = GraphQueryService(".csegraph/index.db").neighborhood(
    "refresh_token",
    depth=1,
)
```

`CodegenService` is available only from the optional `csegraph-codegen` add-on:

```python
from csegraph_codegen import CodegenService
```

The CLI can run without the SDK or codegen add-on installed; `csegraph codegen ...` lazy-loads `csegraph_codegen` and prints an install hint if it is missing.

## How Retrieval Works

1. Parse Python with `ast`; no user repo code is executed.
2. Store repo/folder/file/class/function/method nodes in SQLite.
3. Store edges for `contains`, `imports`, `calls`, `inherits`, `decorates`, and `tested_by`.
4. Rank candidates with FTS5 BM25, exact-name boosts, path/name boosts, and target matching.
5. Expand across graph neighbors using the active profile budget.
6. Return the minimum sufficient context bundle with evidence and raw-code fallbacks.

## Profiles

| Profile | Use when |
|---|---|
| `small` | You want the tightest context and fastest lookup. |
| `medium` | Default agent workflow. |
| `large` | Larger repos or changes that need wider dependency expansion. |

## Development Commands

```bash
# Full test suite.
env/bin/python -m pytest tests/ -q

# Compile check.
env/bin/python -m compileall -q csegraph_core packages/csegraph packages/csegraph-cli packages/csegraph-codegen agents models
```

Additional operational notes for agents live in [`CLAUDE.md`](CLAUDE.md). Architecture details live in [`docs/architecture.md`](docs/architecture.md). CLI/SDK usage lives in [`docs/csegraph.md`](docs/csegraph.md).

## Research And Evaluation Mode

This repository still contains the original research/evaluation pipeline under `agents/`, `models/`, `run_pipeline.py`, and `compare_baselines.py`. That mode is useful for reproducing CSE experiments and sandbox metrics, but it is not the main product surface.

The practical v1.2 line is the SQLite-backed `csegraph-core` engine plus the CLI/SDK context APIs.

## Safety And Scope

- Python only for v1.x.
- Indexing and retrieval are AST-only; user repository code is not executed.
- No daemon or server is required.
- Dense embeddings are optional and not required for default retrieval.
- Code generation is optional; context retrieval is the product.
