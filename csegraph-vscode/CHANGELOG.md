# Changelog

## 1.8.0

- Added the `auto` profile option and made it the extension default.
- Aligned extension version with the Python package release.

## 1.7.2

- Added the Marketplace icon metadata and packaged icon asset.
- Updated install docs to point users at the Marketplace flow first.
- Kept the editable SVG source in the repository while excluding it from the VSIX package.

## 1.7.1

- Bumped package version to 1.7.1 and updated metadata/installation references (README, package.json, package-lock.json).

- Reduced the extension to the core context loop: index, refresh, status, context, and inspect.
- Removed diagnostic commands from the VS Code UI; use `csegraph analyze` for consolidated diagnostics, or repo-local maintainer tooling for low-level analysis.

## 1.0.0

- Historical broad command set before the extension was reduced to index, refresh, status, context, and inspect.
- Status bar with node/edge count and warning indicator
- Auto-refresh on save with configurable debounce
- Right-click context menu for Inspect (Trace Flow was removed before 1.7.1)
- Keybindings: `Ctrl+Shift+G` (context), `Ctrl+Shift+I` (inspect)
- CLI auto-discovery from local virtualenvs (`venv/`, `.venv/`, `env/`, `.env/`)
- Configurable CLI path, profile, and status bar visibility
