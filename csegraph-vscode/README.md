# CseGraph for VS Code

Lightweight VS Code extension for [CseGraph](https://github.com/RishiiShah/CseGraph) — a code graph context engine for coding agents. All graph logic stays in the CLI; the extension is a thin UI layer.

Current extension release: `1.8.0`, aligned with the CseGraph CLI package.

## Prerequisites

- **CseGraph CLI installed** using the
  [platform-specific instructions](../README.md#install).

## Install

Install the CLI first, then install the VS Code extension from the Marketplace.
Build an index in the workspace before using status, context, or inspect
commands.

### Marketplace

```bash
code --install-extension rishiishah.csegraph-vscode
```

Marketplace item:
[`rishiishah.csegraph-vscode`](https://marketplace.visualstudio.com/items?itemName=rishiishah.csegraph-vscode).

### Project Setup

```bash
csegraph install --platform vscode
```

This writes `.vscode/settings.json`, `tasks.json`, and `extensions.json` into your project, merging with existing config.

## First Use

Open the repository folder in VS Code, then run **CseGraph: Build Index** from
the command palette. The index is stored at `.csegraph/index.db` inside the
workspace and should not be committed.

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

> [!NOTE]
> If you have text selected in the editor, both **Get Context for Task** and **Inspect Symbol** will automatically use the active selection as their target. Otherwise, they fall back to the word under the cursor.

## Right-Click Menu

In the editor, right-click to access:

- **Inspect Symbol** — inspects the selected text or word at cursor


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
| `csegraph.profile` | enum | `auto` | Indexing profile (`auto`, `small`, `medium`, `large`) |
| `csegraph.logCommandOutput` | boolean | `true` | Write raw CLI stdout/stderr to the CseGraph output panel |
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
| **VS Code workspace root ≠ repo root** | Auto-discovery uses the active editor's workspace folder when available, then the first workspace folder. Open the folder that contains the venv directly, or set `csegraph.command`. |
| **Installed globally but not on VS Code's PATH** | VS Code inherits the system PATH at launch time. If you installed `csegraph` after opening VS Code, restart VS Code. On Windows, VS Code launched from the Start Menu may not see conda/venv activations from your terminal. |
| **Installed in a conda environment** | Conda environments are not auto-discovered. Set `csegraph.command` to the full path (e.g. `C:\Users\you\miniconda3\envs\myenv\Scripts\csegraph.exe`). |

**Quick fix** — find the path and set it explicitly:

```bash
# In your terminal where csegraph works:
where csegraph        # Windows
which csegraph        # macOS / Linux
```

Then in VS Code Settings (`Ctrl+,`), set **csegraph.command** to the full path returned above.

**Diagnostics** — open the CseGraph output panel (`View → Output → CseGraph`) to see `[cli]` log lines showing which discovery step was used or why discovery failed. Command output is local, but context and inspect output can include task text, symbol names, file paths, and selected code excerpts. Set `csegraph.logCommandOutput` to `false` to hide raw CLI stdout/stderr in that panel.

## License

MIT
