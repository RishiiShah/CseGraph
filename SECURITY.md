# Security Policy

## Supported Versions

CseGraph supports the latest released minor line. Older minor lines receive
security fixes only when a maintainer explicitly announces an extended support
window for that release.

## Reporting a Vulnerability

Report suspected vulnerabilities privately through GitHub Security Advisories
for this repository. Do not file public issues for exploitable vulnerabilities.

Maintainers aim to acknowledge valid reports within seven days. The fix and
release timeline depends on impact, exploitability, and whether the issue is in
the CLI, MCP server, Python facade, VS Code extension, or dependency chain.

Please include:

- Affected version and installation method.
- Reproduction steps or proof-of-concept details.
- Impacted surface: CLI, MCP server, Python facade, or VS Code extension.
- Whether the issue requires malicious repository contents, malicious user input,
  or remote network interaction.

## Security Expectations

- CseGraph is local-first and stores repository indexes under `.csegraph/`.
- Registry and daemon features store local metadata under `~/.csegraph/`,
  including registered repository paths, index database paths, daemon PID files,
  and logs.
- The MCP server runs as a local stdio process; do not expose it to untrusted
  remote clients.
- Generated artifacts such as `.csegraph/`, `.scratch/`, `dist/`, `build/`,
  `.egg-info/`, VSIX files, `.vscode/`, `.cursor/`, `.gemini/`, `.kiro/`,
  `csegraph-vscode/node_modules/`, and `csegraph-vscode/out/` must not be
  committed.
- The VS Code extension is a thin UI around the `csegraph` CLI and should not
  vendor or reimplement graph engine behavior.

## Network and Privacy Notes

Normal indexing, refresh, retrieval, MCP stdio, and VS Code extension commands
run locally. The optional embeddings workflow can call an OpenAI-compatible
endpoint only when explicitly configured and allowed with
`CSEGRAPH_ALLOW_CLOUD_EMBEDDINGS`; that sends symbol text to the configured
endpoint.

The VS Code extension writes command output to the local CseGraph output panel.
For context and inspect commands, that output can include task text, symbol
names, file paths, and selected code excerpts.
