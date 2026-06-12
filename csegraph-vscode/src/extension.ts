import * as vscode from "vscode";
import { execFile } from "child_process";
import * as fs from "fs";
import * as path from "path";

let statusBarItem: vscode.StatusBarItem;
let outputChannel: vscode.OutputChannel;
let refreshTimer: ReturnType<typeof setTimeout> | undefined;
let resolvedCliByRoot = new Map<string, string>();
let loggedFallbacks = new Set<string>();

export function activate(context: vscode.ExtensionContext): void {
  outputChannel = vscode.window.createOutputChannel("CseGraph");

  statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left,
    50
  );
  statusBarItem.command = "csegraph.status";
  statusBarItem.tooltip = "Click for csegraph status";
  context.subscriptions.push(statusBarItem);

  const commands: [string, () => void][] = [
    ["csegraph.index", cmdIndex],
    ["csegraph.refresh", cmdRefresh],
    ["csegraph.status", cmdStatus],
    ["csegraph.context", cmdContext],
    ["csegraph.inspect", cmdInspect],
  ];
  for (const [id, handler] of commands) {
    context.subscriptions.push(
      vscode.commands.registerCommand(id, handler)
    );
  }

  const watcher = vscode.workspace.onDidSaveTextDocument((doc) => {
    const config = vscode.workspace.getConfiguration("csegraph");
    if (!config.get<boolean>("autoRefresh", true)) {
      return;
    }
    const ext = path.extname(doc.fileName).toLowerCase();
    const watched = new Set([
      ".py", ".ts", ".tsx", ".js", ".jsx",
      ".go", ".rs", ".java", ".rb", ".c", ".cpp", ".h",
    ]);
    if (!watched.has(ext)) {
      return;
    }
    const debounce = config.get<number>("refreshDebounce", 2000);
    if (refreshTimer) {
      clearTimeout(refreshTimer);
    }
    refreshTimer = setTimeout(() => {
      silentRefresh();
    }, debounce);
  });
  context.subscriptions.push(watcher);

  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(() => {
      updateStatusBar();
    })
  );
  context.subscriptions.push(
    vscode.workspace.onDidChangeWorkspaceFolders(() => {
      updateStatusBar();
    })
  );

  updateStatusBar();
}

export function deactivate(): void {
  if (refreshTimer) {
    clearTimeout(refreshTimer);
  }
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

function cmdIndex(): void {
  runWithProgress(
    "Building index...",
    "index",
    getProfileArgs(["--postprocess", "full"])
  );
}

function cmdRefresh(): void {
  runWithProgress(
    "Refreshing...",
    "refresh",
    getProfileArgs(["--postprocess", "minimal"])
  );
}

function cmdStatus(): void {
  run("status", ["--verbose"]);
}

async function cmdContext(): Promise<void> {
  const task = await vscode.window.showInputBox({
    prompt: "Describe the coding task you need context for",
    placeHolder: "e.g. fix auth token refresh bug",
  });
  if (!task) {
    return;
  }

  const editor = vscode.window.activeTextEditor;
  const args = [task, "--format", "markdown"];
  if (editor) {
    const selection = editor.document.getText(editor.selection).trim();
    const symbol = selection || getWordAtCursor(editor);
    if (symbol) {
      args.push("--target", symbol);
    }
  }
  run("context", getProfileArgs(args));
}

async function cmdInspect(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  let symbol: string | undefined;

  if (editor) {
    const selection = editor.document.getText(editor.selection).trim();
    symbol = selection || getWordAtCursor(editor);
  }
  if (!symbol) {
    symbol = await vscode.window.showInputBox({
      prompt: "Symbol or node to inspect",
      placeHolder: "e.g. MyClass.method",
    });
  }
  if (!symbol) {
    return;
  }
  run("inspect", [symbol, "--depth", "1", "--detail-level", "standard"]);
}

// ---------------------------------------------------------------------------
// Runner
// ---------------------------------------------------------------------------

function run(command: string, args: string[] = []): void {
  const root = getWorkspaceRoot();
  if (!root) {
    vscode.window.showErrorMessage(
      "Open a workspace folder to use CseGraph."
    );
    return;
  }

  const cli = getCliCommand(root);
  const allArgs = [command, ...args];

  outputChannel.show(true);
  outputChannel.appendLine("");
  outputChannel.appendLine(formatCommandLine(cli, allArgs));
  outputChannel.appendLine("");

  execFile(cli, allArgs, { cwd: root, timeout: 120_000 }, (err, stdout, stderr) => {
    appendCommandOutput(stdout, stderr);
    if (err && err.killed) {
      outputChannel.appendLine("Command timed out.");
    } else if (err) {
      outputChannel.appendLine(`Exit code: ${err.code}`);
    }
    updateStatusBar();
  });
}

function runWithProgress(
  title: string,
  command: string,
  args: string[] = []
): void {
  const root = getWorkspaceRoot();
  if (!root) {
    vscode.window.showErrorMessage(
      "Open a workspace folder to use CseGraph."
    );
    return;
  }

  const cli = getCliCommand(root);
  const allArgs = [command, ...args];

  outputChannel.show(true);
  outputChannel.appendLine("");
  outputChannel.appendLine(formatCommandLine(cli, allArgs));
  outputChannel.appendLine("");

  vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: `CseGraph: ${title}`,
      cancellable: false,
    },
    () =>
      new Promise<void>((resolve) => {
        execFile(
          cli,
          allArgs,
          { cwd: root, timeout: 300_000 },
          (err, stdout, stderr) => {
            appendCommandOutput(stdout, stderr);
            if (err) {
              vscode.window.showErrorMessage(
                `CseGraph ${command} failed. See output panel.`
              );
            } else {
              vscode.window.showInformationMessage(
                `CseGraph: ${command} complete.`
              );
            }
            updateStatusBar();
            resolve();
          }
        );
      })
  );
}

function silentRefresh(): void {
  const root = getWorkspaceRoot();
  if (!root) {
    return;
  }
  const cli = getCliCommand(root);
  execFile(
    cli,
    ["refresh", ...getProfileArgs(["--postprocess", "minimal"])],
    { cwd: root, timeout: 60_000 },
    (_err, _stdout, _stderr) => {
      updateStatusBar();
    }
  );
}

// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------

function updateStatusBar(): void {
  const config = vscode.workspace.getConfiguration("csegraph");
  if (!config.get<boolean>("statusBar", true)) {
    statusBarItem.hide();
    return;
  }

  const root = getWorkspaceRoot();
  if (!root) {
    statusBarItem.hide();
    return;
  }

  const cli = getCliCommand(root);
  execFile(
    cli,
    ["status", "--json"],
    { cwd: root, timeout: 10_000 },
    (err, stdout) => {
      if (err || !stdout.trim()) {
        statusBarItem.text = "$(database) csegraph: no index";
        statusBarItem.show();
        return;
      }
      try {
        const data = JSON.parse(stdout);
        const nodes = data.total_nodes ?? 0;
        const edges = data.total_edges ?? 0;
        const warnings = (data.warnings ?? []).length;
        const icon = warnings > 0 ? "$(warning)" : "$(database)";
        statusBarItem.text = `${icon} csegraph: ${nodes} nodes, ${edges} edges`;
        if (warnings > 0) {
          statusBarItem.tooltip = `${warnings} warning(s) — click for details`;
        } else {
          statusBarItem.tooltip = "Click for csegraph status";
        }
        statusBarItem.show();
      } catch {
        statusBarItem.text = "$(database) csegraph";
        statusBarItem.show();
      }
    }
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getWorkspaceRoot(): string | undefined {
  const editor = vscode.window.activeTextEditor;
  if (editor) {
    const folder = vscode.workspace.getWorkspaceFolder(editor.document.uri);
    if (folder) {
      return folder.uri.fsPath;
    }
  }

  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    return undefined;
  }
  return folders[0].uri.fsPath;
}

function getProfileArgs(args: string[] = []): string[] {
  const config = vscode.workspace.getConfiguration("csegraph");
  const profile = config.get<string>("profile", "medium");
  return [...args, "--profile", profile];
}

function appendCommandOutput(stdout: string, stderr: string): void {
  if (!shouldLogCommandOutput()) {
    if (stdout || stderr) {
      outputChannel.appendLine(
        "Command output hidden by csegraph.logCommandOutput."
      );
    }
    return;
  }
  if (stdout) {
    outputChannel.appendLine(stdout);
  }
  if (stderr) {
    outputChannel.appendLine(stderr);
  }
}

function formatCommandLine(cli: string, allArgs: string[]): string {
  if (!shouldLogCommandOutput()) {
    const command = allArgs[0] ?? "";
    return [">", cli, command, "[arguments hidden]"].filter(Boolean).join(" ");
  }
  return [">", cli, allArgs.join(" ")].join(" ");
}

function shouldLogCommandOutput(): boolean {
  const config = vscode.workspace.getConfiguration("csegraph");
  return config.get<boolean>("logCommandOutput", true);
}

function getCliCommand(root: string): string {
  const config = vscode.workspace.getConfiguration("csegraph");
  const configured = config.get<string>("command", "csegraph");
  if (configured !== "csegraph") {
    outputChannel.appendLine(`[cli] using configured command: ${configured}`);
    return configured;
  }

  const cached = resolvedCliByRoot.get(root);
  if (cached) {
    return cached;
  }

  const isWin = process.platform === "win32";
  const candidates = [
    "venv", ".venv", "env", ".env",
  ];
  for (const dir of candidates) {
    const bin = isWin
      ? path.join(root, dir, "Scripts", "csegraph.exe")
      : path.join(root, dir, "bin", "csegraph");
    if (fs.existsSync(bin)) {
      resolvedCliByRoot.set(root, bin);
      outputChannel.appendLine(`[cli] auto-discovered: ${bin}`);
      return bin;
    }
  }

  // Don't cache the fallback — retry discovery on every call so that
  // workspace folders or venvs created after activation are picked up.
  if (!loggedFallbacks.has(root)) {
    loggedFallbacks.add(root);
    outputChannel.appendLine(
      `[cli] csegraph not found in venv (root=${root ?? "none"}), falling back to PATH`
    );
  }
  return "csegraph";
}

function getWordAtCursor(
  editor: vscode.TextEditor
): string | undefined {
  const position = editor.selection.active;
  const range = editor.document.getWordRangeAtPosition(position);
  if (!range) {
    return undefined;
  }
  return editor.document.getText(range);
}
