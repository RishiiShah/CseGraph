import * as vscode from "vscode";
import { execFile } from "child_process";
import * as fs from "fs";
import * as path from "path";
import {
  PerRootDebouncer,
  StatusUpdateCoordinator,
} from "./coordinators";

let statusBarItem: vscode.StatusBarItem;
let outputChannel: vscode.OutputChannel;
const refreshDebouncer = new PerRootDebouncer<
  ReturnType<typeof setTimeout>
>(
  (callback, delay) => setTimeout(callback, delay),
  (timer) => clearTimeout(timer)
);
let statusCoordinator:
  | StatusUpdateCoordinator<StatusCommandResult>
  | undefined;
let resolvedCliByRoot = new Map<string, string>();
let loggedFallbacks = new Set<string>();

interface StatusCommandResult {
  err: Error | null;
  stdout: string;
}

export function activate(context: vscode.ExtensionContext): void {
  outputChannel = vscode.window.createOutputChannel("CseGraph");

  statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left,
    50
  );
  statusBarItem.command = "csegraph.status";
  statusBarItem.tooltip = "Click for csegraph status";
  context.subscriptions.push(statusBarItem);

  statusCoordinator = new StatusUpdateCoordinator(
    (root, complete) => {
      const cli = getCliCommand(root);
      execFile(
        cli,
        ["status", "--json"],
        { cwd: root, timeout: 10_000 },
        (err, stdout) => complete({ err, stdout })
      );
    },
    (_root, result) => renderStatusBar(result)
  );

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
    ]);
    if (!watched.has(ext)) {
      return;
    }
    const folder = vscode.workspace.getWorkspaceFolder(doc.uri);
    if (!folder) {
      return;
    }
    const root = folder.uri.fsPath;
    const debounce = config.get<number>("refreshDebounce", 2000);
    refreshDebouncer.schedule(root, debounce, () => {
      silentRefresh(root);
    });
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
  refreshDebouncer.clear();
  statusCoordinator?.clear();
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

function cmdIndex(): void {
  runWithProgress("Building index...", "index");
}

function cmdRefresh(): void {
  runWithProgress("Refreshing...", "refresh");
}

function cmdStatus(): void {
  run("status");
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
  run("context", args);
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
  run("graph", [symbol, "--depth", "1"]);
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
    refreshStatusBar(root);
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
            refreshStatusBar(root);
            resolve();
          }
        );
      })
  );
}

function silentRefresh(root: string): void {
  const cli = getCliCommand(root);
  execFile(
    cli,
    ["refresh"],
    { cwd: root, timeout: 60_000 },
    (_err, _stdout, _stderr) => {
      refreshStatusBar(root);
    }
  );
}

// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------

function updateStatusBar(): void {
  const config = vscode.workspace.getConfiguration("csegraph");
  if (!config.get<boolean>("statusBar", true)) {
    statusCoordinator?.show(undefined);
    statusBarItem.hide();
    return;
  }

  const root = getWorkspaceRoot();
  if (!root) {
    statusCoordinator?.show(undefined);
    statusBarItem.hide();
    return;
  }

  statusCoordinator?.show(root);
}

function refreshStatusBar(root: string): void {
  const config = vscode.workspace.getConfiguration("csegraph");
  if (!config.get<boolean>("statusBar", true)) {
    statusCoordinator?.show(undefined);
    statusBarItem.hide();
    return;
  }
  statusCoordinator?.refresh(root);
}

function renderStatusBar(result: StatusCommandResult): void {
  if (result.err || !result.stdout.trim()) {
    statusBarItem.text = "$(database) csegraph: no index";
    statusBarItem.show();
    return;
  }
  try {
    const data = JSON.parse(result.stdout);
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
