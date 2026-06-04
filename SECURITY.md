# Security Policy

## Supported Versions

CseGraph is in public release hardening and currently supports the latest
released minor line only.

| Version | Supported |
| --- | --- |
| 1.7.x | Yes |
| < 1.7 | No |

## Reporting a Vulnerability

Report suspected vulnerabilities privately by opening a GitHub security advisory
or emailing the maintainer listed on the project repository. Do not file public
issues for exploitable vulnerabilities.

Please include:

- Affected version and installation method.
- Reproduction steps or proof-of-concept details.
- Impacted surface: CLI, MCP server, Python facade, or VS Code extension.
- Whether the issue requires malicious repository contents, malicious user input,
  or remote network interaction.

## Security Expectations

- CseGraph is local-first and stores repository indexes under `.csegraph/`.
- The MCP server runs as a local stdio process; do not expose it to untrusted
  remote clients.
- Generated artifacts such as `.csegraph/`, `.scratch/`, `dist/`, `build/`,
  `.egg-info/`, VSIX files, and `csegraph-vscode/out/` must not be committed.
- The VS Code extension is a thin UI around the `csegraph` CLI and should not
  vendor or reimplement graph engine behavior.
