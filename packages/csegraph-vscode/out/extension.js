"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const child_process_1 = require("child_process");
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
let statusBarItem;
let outputChannel;
let refreshTimer;
let resolvedCli;
function activate(context) {
    outputChannel = vscode.window.createOutputChannel("CseGraph");
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
    statusBarItem.command = "csegraph.status";
    statusBarItem.tooltip = "Click for csegraph status";
    context.subscriptions.push(statusBarItem);
    const commands = [
        ["csegraph.index", cmdIndex],
        ["csegraph.refresh", cmdRefresh],
        ["csegraph.status", cmdStatus],
        ["csegraph.context", cmdContext],
        ["csegraph.inspect", cmdInspect],
    ];
    for (const [id, handler] of commands) {
        context.subscriptions.push(vscode.commands.registerCommand(id, handler));
    }
    const watcher = vscode.workspace.onDidSaveTextDocument((doc) => {
        const config = vscode.workspace.getConfiguration("csegraph");
        if (!config.get("autoRefresh", true)) {
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
        const debounce = config.get("refreshDebounce", 2000);
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
function deactivate() {
    if (refreshTimer) {
        clearTimeout(refreshTimer);
    }
}
// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------
function cmdIndex() {
    runWithProgress("Building index...", "index", ["--postprocess", "full"]);
}
function cmdRefresh() {
    runWithProgress("Refreshing...", "refresh", ["--postprocess", "minimal"]);
}
function cmdStatus() {
    run("status", ["--verbose"]);
}
async function cmdContext() {
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
        const symbol = getWordAtCursor(editor);
        if (symbol) {
            args.push("--target", symbol);
        }
    }
    run("context", args);
}
async function cmdInspect() {
    const editor = vscode.window.activeTextEditor;
    let symbol;
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
// ---------------------------------------------------------------------------
// Runner
// ---------------------------------------------------------------------------
function run(command, args = []) {
    const root = getWorkspaceRoot();
    if (!root) {
        vscode.window.showErrorMessage("Open a workspace folder to use CseGraph.");
        return;
    }
    const cli = getCliCommand();
    const allArgs = [command, ...args];
    outputChannel.show(true);
    outputChannel.appendLine("");
    outputChannel.appendLine(`> ${cli} ${allArgs.join(" ")}`);
    outputChannel.appendLine("");
    (0, child_process_1.execFile)(cli, allArgs, { cwd: root, timeout: 120000 }, (err, stdout, stderr) => {
        if (stdout) {
            outputChannel.appendLine(stdout);
        }
        if (stderr) {
            outputChannel.appendLine(stderr);
        }
        if (err && err.killed) {
            outputChannel.appendLine("Command timed out.");
        }
        else if (err) {
            outputChannel.appendLine(`Exit code: ${err.code}`);
        }
        updateStatusBar();
    });
}
function runWithProgress(title, command, args = []) {
    const root = getWorkspaceRoot();
    if (!root) {
        vscode.window.showErrorMessage("Open a workspace folder to use CseGraph.");
        return;
    }
    const cli = getCliCommand();
    const allArgs = [command, ...args];
    outputChannel.show(true);
    outputChannel.appendLine("");
    outputChannel.appendLine(`> ${cli} ${allArgs.join(" ")}`);
    outputChannel.appendLine("");
    vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification,
        title: `CseGraph: ${title}`,
        cancellable: false,
    }, () => new Promise((resolve) => {
        (0, child_process_1.execFile)(cli, allArgs, { cwd: root, timeout: 300000 }, (err, stdout, stderr) => {
            if (stdout) {
                outputChannel.appendLine(stdout);
            }
            if (stderr) {
                outputChannel.appendLine(stderr);
            }
            if (err) {
                vscode.window.showErrorMessage(`CseGraph ${command} failed. See output panel.`);
            }
            else {
                vscode.window.showInformationMessage(`CseGraph: ${command} complete.`);
            }
            updateStatusBar();
            resolve();
        });
    }));
}
function silentRefresh() {
    const root = getWorkspaceRoot();
    if (!root) {
        return;
    }
    const cli = getCliCommand();
    (0, child_process_1.execFile)(cli, ["refresh", "--postprocess", "minimal"], { cwd: root, timeout: 60000 }, (_err, _stdout, _stderr) => {
        updateStatusBar();
    });
}
// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------
function updateStatusBar() {
    const config = vscode.workspace.getConfiguration("csegraph");
    if (!config.get("statusBar", true)) {
        statusBarItem.hide();
        return;
    }
    const root = getWorkspaceRoot();
    if (!root) {
        statusBarItem.hide();
        return;
    }
    const cli = getCliCommand();
    (0, child_process_1.execFile)(cli, ["status", "--json"], { cwd: root, timeout: 10000 }, (err, stdout) => {
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
            }
            else {
                statusBarItem.tooltip = "Click for csegraph status";
            }
            statusBarItem.show();
        }
        catch {
            statusBarItem.text = "$(database) csegraph";
            statusBarItem.show();
        }
    });
}
// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function getWorkspaceRoot() {
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) {
        return undefined;
    }
    return folders[0].uri.fsPath;
}
function getCliCommand() {
    if (resolvedCli) {
        return resolvedCli;
    }
    const config = vscode.workspace.getConfiguration("csegraph");
    const configured = config.get("command", "csegraph");
    if (configured !== "csegraph") {
        resolvedCli = configured;
        outputChannel.appendLine(`[cli] using configured command: ${resolvedCli}`);
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
                resolvedCli = bin;
                outputChannel.appendLine(`[cli] auto-discovered: ${resolvedCli}`);
                return resolvedCli;
            }
        }
    }
    // Don't cache the fallback — retry discovery on every call so that
    // workspace folders or venvs created after activation are picked up.
    outputChannel.appendLine(`[cli] csegraph not found in venv (root=${root ?? "none"}), falling back to PATH`);
    return "csegraph";
}
function getWordAtCursor(editor) {
    const position = editor.selection.active;
    const range = editor.document.getWordRangeAtPosition(position);
    if (!range) {
        return undefined;
    }
    return editor.document.getText(range);
}
//# sourceMappingURL=extension.js.map