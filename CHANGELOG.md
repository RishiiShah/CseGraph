# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- Added package-health badges to the README.
- Added a `py.typed` marker and `Typing :: Typed` classifier for PEP 561 type
  checker support.
- Added Python 3.13 and 3.14 package classifiers, and added Python 3.13 to the
  CI matrix.
- Added GitHub issue templates, a pull request template, and Dependabot updates
  for GitHub Actions, Python, and VS Code extension dependencies.
- Added optional developer tooling for Ruff, mypy, coverage, and pre-commit.
- Added CI checks for Ruff and test coverage.

### Changed

- Changed MCP install behavior so Codex config is repo-local at
  `.codex/config.toml` instead of global user config.
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
