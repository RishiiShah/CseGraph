import * as vscode from "vscode";
import { exec } from "child_process";
import * as fs from "fs";
import * as path from "path";

let statusBarItem: vscode.StatusBarItem;
let outputChannel: vscode.OutputChannel;
let refreshTimer: ReturnType<typeof setTimeout> | undefined;
let resolvedCli: string | undefined;

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
    ["csegraph.flows", cmdFlows],
    ["csegraph.flowsHere", cmdFlowsHere],
    ["csegraph.inspect", cmdInspect],
    ["csegraph.vulnerabilities", cmdVulnerabilities],
    ["csegraph.architecture", cmdArchitecture],
    ["csegraph.testGaps", cmdTestGaps],
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
  runWithProgress("Building index...", "index", ["--postprocess", "full"]);
}

function cmdRefresh(): void {
  runWithProgress("Refreshing...", "refresh", ["--postprocess", "minimal"]);
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
  const args = [JSON.stringify(task), "--format", "markdown"];
  if (editor) {
    const symbol = getWordAtCursor(editor);
    if (symbol) {
      args.push("--target", symbol);
    }
  }
  run("context", args);
}

function cmdFlows(): void {
  run("flows", ["--limit", "10"]);
}

async function cmdFlowsHere(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage("No active editor.");
    return;
  }
  const symbol = getWordAtCursor(editor);
  if (!symbol) {
    vscode.window.showWarningMessage("No symbol at cursor.");
    return;
  }
  run("flows", ["--entry-point", symbol, "--limit", "5"]);
}

async function cmdInspect(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  let symbol: string | undefined;

  if (editor) {
    symbol = getWordAtCursor(editor);
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

function cmdVulnerabilities(): void {
  run("vulnerabilities", ["--limit", "20"]);
}

function cmdArchitecture(): void {
  run("architecture", ["--limit", "10"]);
}

function cmdTestGaps(): void {
  run("test-gaps", ["--limit", "15"]);
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

  const cli = getCliCommand();
  const fullCmd = [cli, command, ...args].join(" ");

  outputChannel.show(true);
  outputChannel.appendLine("");
  outputChannel.appendLine(`> ${fullCmd}`);
  outputChannel.appendLine("");

  exec(fullCmd, { cwd: root, timeout: 120_000 }, (err, stdout, stderr) => {
    if (stdout) {
      outputChannel.appendLine(stdout);
    }
    if (stderr) {
      outputChannel.appendLine(stderr);
    }
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

  const cli = getCliCommand();
  const fullCmd = [cli, command, ...args].join(" ");

  outputChannel.show(true);
  outputChannel.appendLine("");
  outputChannel.appendLine(`> ${fullCmd}`);
  outputChannel.appendLine("");

  vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: `CseGraph: ${title}`,
      cancellable: false,
    },
    () =>
      new Promise<void>((resolve) => {
        exec(
          fullCmd,
          { cwd: root, timeout: 300_000 },
          (err, stdout, stderr) => {
            if (stdout) {
              outputChannel.appendLine(stdout);
            }
            if (stderr) {
              outputChannel.appendLine(stderr);
            }
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
  const cli = getCliCommand();
  exec(
    `${cli} refresh --postprocess minimal`,
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

  const cli = getCliCommand();
  exec(
    `${cli} status --json`,
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
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    return undefined;
  }
  return folders[0].uri.fsPath;
}

function getCliCommand(): string {
  if (resolvedCli) {
    return resolvedCli;
  }

  const config = vscode.workspace.getConfiguration("csegraph");
  const configured = config.get<string>("command", "csegraph");
  if (configured !== "csegraph") {
    resolvedCli = configured;
    return resolvedCli;
  }

  const root = getWorkspaceRoot();
  if (root) {
    const isWin = process.platform === "win32";
    const candidates = [
      "venv", ".venv", "env", ".env",
    ];
    for (const dir of candidates) {
      const bin = isWin
        ? path.join(root, dir, "Scripts", "csegraph.exe")
        : path.join(root, dir, "bin", "csegraph");
      if (fs.existsSync(bin)) {
        resolvedCli = `"${bin}"`;
        return resolvedCli;
      }
    }
  }

  resolvedCli = "csegraph";
  return resolvedCli;
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
