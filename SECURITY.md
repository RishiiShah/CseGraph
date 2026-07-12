# Security Policy

## Supported Versions

Only the latest released minor line receives security fixes.

| Version | Supported |
|---|---|
| 2.0.x | Yes |
| < 2.0 | No |

If an older release receives an extended support window, maintainers will
announce it explicitly.

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do not open a public GitHub issue.**
2. Use
   [GitHub private vulnerability reporting](https://github.com/RishiiShah/CseGraph/security/advisories/new)
   through the repository's **Security** tab.
3. Include:
   - A description of the vulnerability
   - The affected CseGraph version and installation method
   - Steps to reproduce or a proof of concept
   - The affected surface: CLI, MCP server, Python API, or VS Code extension
   - Potential impact
   - Whether exploitation requires a malicious repository, local user input, or
     network access
   - A suggested fix, if you have one

Maintainers aim to acknowledge valid reports within seven days. Remediation and
release timing depends on severity, exploitability, and the affected component.

## Security Model

CseGraph is a local development tool. Its normal workflow:

- Runs the MCP server over local stdio; it does not open an MCP network listener
- Stores repository indexes in `.csegraph/index.db`
- Reads Python, JavaScript, and TypeScript source files from the selected
  repository
- Writes fresh indexes beside the active database before atomic replacement
- May write repo-local `.scratch/csegraph/` helper paths when an explicit
  database path or maintainer workflow uses that location
- Writes generated MCP/client setup files only when `csegraph install` is run
- Makes no network requests during normal indexing, refresh, retrieval, MCP, or
  VS Code operations

`csegraph install` can create or update project-local MCP/client config such as
`.codex/config.toml`, `.mcp.json`, `.cursor/mcp.json`,
`.gemini/settings.json`, `.kiro/settings/mcp.json`,
`.agents/mcp_config.json`, `.vscode/` files, instruction files, and optional
agent refresh hooks under `.claude/settings.json` or `.codex/hooks.json`.
`--platform antigravity-ide` is the explicit global-config exception.

Repository contents are untrusted input. CseGraph can return source text,
symbol names, import names, and paths to a coding agent. Agents and users
should treat that content as data, not as trusted instructions.

### Mitigations

| Vector | Mitigation |
|---|---|
| Path traversal | Source reads resolve paths and reject files outside the repository root. Database paths are restricted to repository-local `.csegraph/` or `.scratch/csegraph/` locations. |
| SQL injection | User-derived query values use SQLite parameters. Dynamically generated SQL is limited to internal placeholders and fixed schema identifiers. |
| Subprocess injection | Internal subprocess calls use argument lists. Generated agent-hook commands quote arguments and suppress refresh failures. |
| MCP surface expansion | The stdio server exposes an explicit allowlist of six core tools. Unknown tool names are rejected. |
| MCP input bloat | Every MCP tool schema rejects unknown properties. |
| Oversized context responses | Context, diagnostics, and continuations share one bounded response token budget. |
| Unsupported database schema | Missing or incompatible CseGraph schemas are rejected with `index_required`; users must rebuild with `csegraph index`. |
| Supply-chain publishing | PyPI releases use GitHub trusted publishing with short-lived OIDC credentials. Release artifacts are built, inspected, and uploaded by CI. |

### Trust Boundaries and Limitations

- CseGraph does not remove prompt-injection text from source code. Coding agents
  must continue to treat repository content as untrusted.
- A malicious local user who can modify the repository, its index, generated
  client configuration, or generated agent hooks is outside CseGraph's trust
  boundary.
- The MCP server is intended for local stdio clients. Do not wrap or expose it
  as an unauthenticated remote service.
- The VS Code output panel can contain task text, symbol names, file paths, and
  selected code excerpts.

## Optional Network Access

CseGraph is local-first, with these opt-in or user-triggered exceptions:

- **Package and extension installation:** Package managers and the VS Code
  Marketplace use their normal network services.
- **Release and benchmark maintenance:** CI fetches tags, installs pinned
  tooling, and can run maintainer benchmark workflows. Normal product indexing,
  refresh, retrieval, and MCP serving do not require this access.

## Security Checks

The CI pipeline runs:

- The full Python test suite with coverage on Python 3.13
- Compatibility smoke tests on Python 3.10, 3.11, 3.12, and 3.14
- Ruff lint and formatting checks
- Mypy type checking
- Adaptive retrieval benchmark gates
- Coverage execution
- Package layout and generated-artifact guardrails
- Wheel and source-distribution inspection before release
- `npm audit --audit-level=high` for the VS Code extension
- VS Code extension linting, compilation, and package inspection

GitHub Actions use read-only repository permissions by default. PyPI publishing
receives `id-token: write` only in the dedicated trusted-publishing job.

CseGraph does not currently run Bandit or a dedicated Python dependency
vulnerability scanner in CI. Reports or contributions that improve these checks
are welcome.
