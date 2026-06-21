## Summary

-

## Scope

- [ ] Python package, CLI, MCP server, or SDK facade
- [ ] VS Code extension
- [ ] Documentation or community files
- [ ] Tests, fixtures, or benchmarks

## Validation

- [ ] `env/bin/python -m pytest tests/ -q`
- [ ] `env/bin/python -m compileall -q csegraph tools csegraph-vscode`
- [ ] `cd csegraph-vscode && npm run lint && npm run compile`
- [ ] Not run; explain why:

## CseGraph Checklist

- [ ] Keeps the public package boundary to `csegraph` and documented facade exports.
- [ ] Does not commit generated or local-only files such as `.csegraph/`, `.scratch/`, `.vscode/`, `dist/`, `build/`, `*.egg-info/`, `*.vsix`, or `csegraph-vscode/out/`.
- [ ] Notes any local-first/privacy impact, including optional embeddings or log/output changes.
- [ ] Updates docs or examples when CLI, MCP, SDK, or extension behavior changes.
