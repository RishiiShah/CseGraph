# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

## 1.8.1 - 2026-06-24

### Added

- Added platform-scoped MCP setup and doctor checks for Codex, Claude Code,
  Cursor, Gemini CLI, Kiro, Copilot, and Antigravity, including host-specific
  observed-call evidence.
- Added cross-platform MCP launcher resolution for macOS, Linux, Windows,
  PowerShell, user-script installs, and common project virtualenv directories.
- Added per-context `token_usage` and per-MCP-session `session_token_usage`
  estimates so agents can report approximate tokens used and saved.
- Added install `next_steps` guidance that tells users to enable or approve the
  generated MCP server in the target host and verify the six CseGraph tools.
- Added hosted OS MCP install smoke coverage for Ubuntu, macOS, and Windows CI.

### Changed

- Changed watch logging to keep the `INFO:` tag while hiding internal logger
  names and suppressing noisy `watchfiles.main` messages by default.
- Changed generated agent guidance and MCP response trust metadata to treat
  `.csegraph/index.db` as a private implementation detail behind the MCP tools.
- Changed default tests so self-corpus dogfood benchmarks are opt-in; sandbox
  benchmark coverage is the default evidence path.

### Removed

- Removed the old self token-reduction benchmark document now that sandbox
  benchmark results are the canonical measurement.

## 1.8.0 - 2026-06-21

### Added

- Added dynamic `auto` profile selection for MCP, CLI context retrieval, and
  editor defaults so small, medium, and large repositories get safer defaults.
- Added package-health badges to the README.
- Added a `py.typed` marker and `Typing :: Typed` classifier for PEP 561 type
  checker support.
- Added Python 3.13 and 3.14 package classifiers, and added Python 3.13 to the
  CI matrix.
- Added GitHub issue templates, a pull request template, and Dependabot updates
  for GitHub Actions, Python, and VS Code extension dependencies.
- Added optional developer tooling for Ruff, mypy, coverage, and pre-commit.
- Added CI checks for Ruff and test coverage.
- Added a SQLite schema migration framework for known older csegraph index
  versions.
- Added optional tree-sitter grammar extras so installs can stay small while
  `csegraph[all]` preserves full language coverage.
- Added structured diagnostic logging with global `--verbose` and `--quiet`
  controls.
- Added thread-backed async SDK facades for indexing, refreshing, context
  retrieval, and graph queries.
- Added a public parser plugin API for custom parser and tree-sitter language
  registration.
- Added a benchmark regression checker and dedicated CI job for context-quality
  thresholds.
- Added a MkDocs Material documentation site scaffold with strict CI build.
- Added regression coverage for the interactive HTML graph explorer.
- Added monorepo include-root support for indexing and refreshing selected
  repo-local subtrees.
- Added a minimal stdio LSP server with indexed document-symbol support.

### Changed

- Changed MCP install behavior so Codex config is repo-local at
  `.codex/config.toml` instead of global user config.
- Split MCP prompt rendering and tool schemas out of the server app module.
- Changed `csegraph install --platform auto` to create repo-local MCP config for
  Codex, Claude Code, Cursor, Gemini CLI, Kiro, and Copilot.
- Changed `csegraph install --platform <client>` to perform full platform setup
  by default, including platform guidance and supported lifecycle hooks.
- Changed `csegraph install` to add generated local setup paths and `.csegraph/`
  to `.gitignore` by default.

## 1.7.1

### Added

- Consolidated Python packaging into one distribution: `csegraph`.
- Added context-quality benchmark corpus support for maintainer evaluation.
- Added source-first package-layout guardrails and release-hardening workflows.

### Changed

- Moved implementation internals under private namespaces: `csegraph._core`
  and `csegraph._cli`.
- Moved the VS Code extension to the root sibling project `csegraph-vscode/`.
- Kept tracked source to one Python package and one sibling VS Code extension.
- Aligned README, agent docs, architecture notes, and command reference with the
  one-package layout.
- Kept the public Python facade at `import csegraph`.
