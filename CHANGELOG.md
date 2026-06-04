# Changelog

## 1.7.1

- Consolidated Python packaging into one distribution: `csegraph`.
- Moved implementation internals under private namespaces: `csegraph._core`
  and `csegraph._cli`.
- Kept the public Python facade at `import csegraph`.
- Moved the VS Code extension to the root sibling project `csegraph-vscode/`.
- Kept tracked source to one Python package and one sibling VS Code extension.
- Added context-quality benchmark corpus support for maintainer evaluation.
- Added source-first package-layout guardrails and release-hardening workflows.
- Aligned README, agent docs, architecture notes, and command reference with the
  one-package layout.
