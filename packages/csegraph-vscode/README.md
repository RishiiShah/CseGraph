# CseGraph for VS Code

Lightweight VS Code extension for [CseGraph](https://github.com/apocalypse44/CseGraph) — a code graph context engine for coding agents. All graph logic stays in the CLI; the extension is a thin UI layer.

## Prerequisites

- **CseGraph CLI** installed (`pip install csegraph` or from source)
- A built index in your workspace (`.csegraph/index.db`)

## Install

### From VSIX

```bash
cd packages/csegraph-vscode
npm install && npm run package
code --install-extension csegraph-vscode-1.0.0.vsix
```

### Via CLI

```bash
csegraph install --platform vscode
```

This writes `.vscode/settings.json`, `tasks.json`, and `extensions.json` into your project, merging with existing config.

## Commands

Open the command palette (`Ctrl+Shift+P`) and type "CseGraph":

| Command | Description |
|---------|-------------|
| **Build Index** | Full index with postprocessing |
| **Refresh Changed Files** | Incremental refresh (minimal postprocess) |
| **Show Status** | Node/edge counts, warnings, staleness |
| **Get Context for Task** | Describe a task, get relevant graph context |
| **Trace Execution Flows** | List top execution flows by criticality |
| **Trace Flow from This Function** | Trace flows starting from the symbol at cursor |
| **Inspect Symbol** | Show callers, callees, and edges for a symbol |
| **Scan Vulnerabilities** | List security-sensitive nodes |
| **Architecture Overview** | Community summaries and coupling analysis |
| **Show Test Gaps** | Untested or under-tested symbols |

## Keybindings

| Shortcut | Command |
|----------|---------|
| `Ctrl+Shift+G` (`Cmd+Shift+G` on Mac) | Get Context for Task |
| `Ctrl+Shift+I` (`Cmd+Shift+I` on Mac) | Inspect Symbol |

## Right-Click Menu

In the editor, right-click to access:

- **Inspect Symbol** — inspects the word at cursor
- **Trace Flow from This Function** — traces flows from the word at cursor

## Status Bar

A status bar item shows the current graph state:

- `$(database) csegraph: 142 nodes, 387 edges` — healthy index
- `$(warning) csegraph: 142 nodes, 387 edges` — index has warnings (click for details)
- `csegraph: no index` — no index built yet

Updates automatically after every command and auto-refresh.

## Auto-Refresh

When enabled (default), saving a supported file (`.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.go`, `.rs`, `.java`, `.rb`, `.c`, `.cpp`, `.h`) triggers `csegraph refresh --postprocess minimal` after a configurable debounce.

## Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `csegraph.command` | string | `csegraph` | Path to the CLI executable |
| `csegraph.profile` | enum | `medium` | Indexing profile (`small`, `medium`, `large`) |
| `csegraph.autoRefresh` | boolean | `true` | Refresh index on file save |
| `csegraph.refreshDebounce` | number | `2000` | Debounce interval in ms |
| `csegraph.statusBar` | boolean | `true` | Show status bar item |

## CLI Discovery

The extension resolves the CLI in this order:

1. `csegraph.command` setting (if changed from default)
2. Local virtualenv: `venv/`, `.venv/`, `env/`, `.env/` in the workspace root
3. System `csegraph` on PATH

## License

MIT
