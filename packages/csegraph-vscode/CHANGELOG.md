# Changelog

## 1.7.0

- Reduced the extension to the core context loop: index, refresh, status, context, and inspect.
- Removed diagnostic commands from the VS Code UI; use the CLI for flows, vulnerabilities, architecture, and test-gap analysis.

## 1.0.0

- 10 commands: index, refresh, status, context, flows, flowsHere, inspect, vulnerabilities, architecture, testGaps
- Status bar with node/edge count and warning indicator
- Auto-refresh on save with configurable debounce
- Right-click context menu for Inspect and Trace Flow
- Keybindings: `Ctrl+Shift+G` (context), `Ctrl+Shift+I` (inspect)
- CLI auto-discovery from local virtualenvs (`venv/`, `.venv/`, `env/`, `.env/`)
- Configurable CLI path, profile, and status bar visibility
