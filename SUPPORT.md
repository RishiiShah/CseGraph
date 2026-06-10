# Support

## Where to Ask

- Usage questions and reproducible bugs: open a GitHub issue.
- Security vulnerabilities: use GitHub Security Advisories; do not open a
  public issue for exploitable vulnerabilities.
- Design changes or new public surfaces: open an issue before starting a large
  pull request.

## Supported Scope

The supported public project is:

- The `csegraph` Python distribution.
- The `csegraph` CLI.
- The public `import csegraph` facade.
- The MCP server exposed by `csegraph serve`.
- The VS Code extension source in `csegraph-vscode/`.

Private modules under `csegraph._core` and `csegraph._cli` may change without
compatibility guarantees.

## Before Opening an Issue

Please include:

- CseGraph version: `csegraph --version`.
- Python version and operating system.
- The command you ran and the full error output.
- Whether the repository has an existing `.csegraph/index.db`.
- A small reproduction repository or file snippet when possible.

Generated local files such as `.csegraph/`, `.scratch/`, `.vscode/`, `.cursor/`,
`.gemini/`, and `.kiro/` should stay out of issues unless the exact contents
are needed to diagnose the problem.
