# CseGraph for VS Code

Lightweight VS Code extension for [CseGraph](https://github.com/RishiiShah/CseGraph) — a code graph context engine for coding agents. All graph logic stays in the CLI; the extension is a thin UI layer.

## Prerequisites

- **CseGraph CLI** installed
  - Install the package: `pip install csegraph`
  - Or install from source at the repository root: `env/bin/pip install -e .`
- A built index in your workspace (`.csegraph/index.db`)

## Install

Install the CLI first, then install the VS Code extension.

### From VSIX

```bash
pip install csegraph
cd csegraph-vscode
npm ci && npm run package
code --install-extension csegraph-vscode-1.7.1.vsix
```

### Via CLI

```bash
pip install csegraph
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
| **Inspect Symbol** | Show callers, callees, and edges for a symbol |

## Keybindings

| Shortcut | Command |
|----------|---------|
| `Ctrl+Shift+G` (`Cmd+Shift+G` on Mac) | Get Context for Task |
| `Ctrl+Shift+I` (`Cmd+Shift+I` on Mac) | Inspect Symbol |

## Right-Click Menu

In the editor, right-click to access:

- **Inspect Symbol** — inspects the word at cursor

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

1. **`csegraph.command` setting** — if you changed it from the default `csegraph`, that value is used as-is.
2. **Local virtualenv** — checks `venv/`, `.venv/`, `env/`, `.env/` inside the workspace root for `Scripts/csegraph.exe` (Windows) or `bin/csegraph` (Unix).
3. **System PATH** — falls back to bare `csegraph`.

Steps 2 and 3 are re-evaluated on every command until a virtualenv binary is found, so creating a venv after the extension activates will be picked up automatically.

### Troubleshooting: `'csegraph' is not recognized`

If commands fail with `'csegraph' is not recognized as an internal or external command`, the extension could not find the CLI. Common causes:

| Cause | Fix |
|-------|-----|
| **Venv has a non-standard name** (e.g. `.conda/`, `myenv/`) | Rename it to one of the auto-discovered names (`venv/`, `.venv/`, `env/`, `.env/`), or set `csegraph.command` to the full path. |
| **VS Code workspace root ≠ repo root** | Auto-discovery looks in the first workspace folder. If you opened a parent directory, the venv won't be found. Open the folder that contains the venv directly, or set `csegraph.command`. |
| **Installed globally but not on VS Code's PATH** | VS Code inherits the system PATH at launch time. If you installed `csegraph` after opening VS Code, restart VS Code. On Windows, VS Code launched from the Start Menu may not see conda/venv activations from your terminal. |
| **Installed in a conda environment** | Conda environments are not auto-discovered. Set `csegraph.command` to the full path (e.g. `C:\Users\you\miniconda3\envs\myenv\Scripts\csegraph.exe`). |

**Quick fix** — find the path and set it explicitly:

```bash
# In your terminal where csegraph works:
where csegraph        # Windows
which csegraph        # macOS / Linux
```

Then in VS Code Settings (`Ctrl+,`), set **csegraph.command** to the full path returned above.

**Diagnostics** — open the CseGraph output panel (`View → Output → CseGraph`) to see `[cli]` log lines showing which discovery step was used or why discovery failed.

## License

MIT
