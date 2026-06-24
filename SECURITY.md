# Security Policy

## Supported Versions

Only the latest released minor line receives security fixes.

| Version | Supported |
|---|---|
| 1.8.x | Yes |
| < 1.8 | No |

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
- Stores registry, daemon PID, and daemon log data under `~/.csegraph/`
- Reads source files from the selected repository
- Writes indexes, exports, scratch data, and generated client configuration
  inside the repository
- Makes no network requests during normal indexing, refresh, retrieval, MCP, or
  VS Code operations

Repository contents are untrusted input. CseGraph can return source text,
symbol names, documentation, and paths to a coding agent. Agents and users
should treat that content as data, not as trusted instructions.

### Mitigations

| Vector | Mitigation |
|---|---|
| Path traversal | Source reads resolve paths and reject files outside the repository root. MCP database paths and export destinations are restricted to repository-local paths. |
| SQL injection | User-derived query values use SQLite parameters. Dynamically generated SQL is limited to internal placeholders and fixed schema identifiers. |
| Subprocess injection | Git and daemon subprocesses use argument lists instead of `shell=True`, with timeouts where commands can block. |
| Daemon file traversal | Registry aliases used for PID and log filenames are restricted to alphanumeric characters, `_`, `-`, and `.`; `..` is rejected. |
| Accidental cloud egress | Non-local embedding endpoints are rejected unless `CSEGRAPH_ALLOW_CLOUD_EMBEDDINGS=1` is explicitly set. |
| Model code execution | Local `sentence-transformers` models are loaded with `trust_remote_code=False`. |
| MCP surface expansion | The stdio server exposes an explicit allowlist of six core tools. Unknown tool names are rejected. |
| Oversized MCP responses | MCP responses support a hard `max_bytes` ceiling and deterministic field truncation. |
| Unsupported database schema | Unknown or incompatible CseGraph schemas are rejected unless an explicit reset path is used. |
| Supply-chain publishing | PyPI releases use GitHub trusted publishing with short-lived OIDC credentials. Release artifacts are built, inspected, and uploaded by CI. |

### Trust Boundaries and Limitations

- CseGraph does not remove prompt-injection text from source code. Coding agents
  must continue to treat repository content as untrusted.
- A malicious local user who can modify the repository, its index, generated
  client configuration, or `~/.csegraph/` state is outside CseGraph's trust
  boundary.
- Generated HTML graph and tree exports contain indexed repository metadata.
  Treat exports from untrusted repositories as untrusted HTML and open them only
  in an appropriately isolated browser context.
- The MCP server is intended for local stdio clients. Do not wrap or expose it
  as an unauthenticated remote service.
- The VS Code output panel can contain task text, symbol names, file paths, and
  selected code excerpts.

## Optional Network Access

CseGraph is local-first, with these opt-in or user-triggered exceptions:

- **OpenAI-compatible embeddings:** A configured endpoint receives symbol names,
  signatures, docstrings, symbol kinds, and file paths. Non-localhost endpoints
  require `CSEGRAPH_ALLOW_CLOUD_EMBEDDINGS=1`.
- **Local embedding model download:** Installing or first using
  `sentence-transformers` may download model files from Hugging Face.
- **HTML graph fonts:** Opening an exported interactive graph can request fonts
  from Google Fonts. The graph data and visualization logic remain embedded in
  the local HTML file.
- **Package and extension installation:** Package managers and the VS Code
  Marketplace use their normal network services.

## Security Checks

The CI pipeline runs:

- The full Python test suite on Python 3.10 through 3.14
- Ruff lint and formatting checks
- Mypy type checking
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
