/**
 * Fabrique Factory — VS Code Extension entry point.
 *
 * Exposes five commands that connect the VS Code editor to the Fabrique backend:
 *
 * | Command                    | Description                                          |
 * |----------------------------|------------------------------------------------------|
 * | `factory.connect`          | Health-check the backend and update the status bar.  |
 * | `factory.runTask`          | Submit the selected text as an AI generation story.  |
 * | `factory.showMemory`       | Keyword search over episodic Engram memory.          |
 * | `factory.startSprint`      | Create a new 2-week sprint via the REST API.         |
 * | `factory.streamGlassroom`  | Open an integrated terminal streaming Glassroom SSE. |
 *
 * The status bar item (bottom-left) reflects the current connection state.
 */

import * as vscode from 'vscode';

/** Shared status bar item showing Factory connection state. */
let statusBarItem: vscode.StatusBarItem;

/** Base URL of the Fabrique backend REST API. */
let backendUrl: string;

/**
 * Placeholder for an EventSource SSE connection.
 * Currently unused — SSE streaming is delegated to a terminal + curl.
 * Kept for future native SSE support in a Webview panel.
 */
let eventSource: EventSource | null;

/**
 * Extension activation entry point — called once by VS Code when the extension loads.
 *
 * Reads the ``factory.backendUrl`` setting, creates the status bar item, and
 * registers all five commands.
 *
 * @param context Extension context used for subscription lifecycle management.
 */
export function activate(context: vscode.ExtensionContext) {
    const config = vscode.workspace.getConfiguration('factory');
    backendUrl = config.get('factory.backendUrl', 'http://localhost:8000');
    
    statusBarItem = vscode.window.createStatusBarItem(
        vscode.StatusBarAlignment.Left,
        100
    );
    statusBarItem.text = '$(vm) Factory: Disconnected';
    statusBarItem.tooltip = 'Click to connect to Factory backend';
    statusBarItem.command = 'factory.connect';
    statusBarItem.show();
    
    context.subscriptions.push(
        vscode.commands.registerCommand('factory.connect', async () => {
            await connectToFactory();
        })
    );
    
    context.subscriptions.push(
        vscode.commands.registerCommand('factory.runTask', async () => {
            await runFactoryTask();
        })
    );
    
    context.subscriptions.push(
        vscode.commands.registerCommand('factory.showMemory', async () => {
            await showMemorySearch();
        })
    );
    
    context.subscriptions.push(
        vscode.commands.registerCommand('factory.startSprint', async () => {
            await startSprint();
        })
    );
    
    context.subscriptions.push(
        vscode.commands.registerCommand('factory.streamGlassroom', async () => {
            await streamGlassroom();
        })
    );
    
    vscode.window.showInformationMessage('Factory System extension activated');
}

/**
 * Ping the backend ``/health`` endpoint and update the status bar accordingly.
 *
 * Sets the status bar to:
 * - `"Factory: Connected"` on HTTP 200.
 * - `"Factory: Error"` on non-OK HTTP status.
 * - `"Factory: Disconnected"` on network failure.
 */
async function connectToFactory() {
    try {
        const response = await fetch(`${backendUrl}/health`);
        if (response.ok) {
            statusBarItem.text = '$(vm) Factory: Connected';
            vscode.window.showInformationMessage('Connected to Factory backend');
        } else {
            statusBarItem.text = '$(vm) Factory: Error';
            vscode.window.showErrorMessage('Factory backend returned error');
        }
    } catch (error) {
        statusBarItem.text = '$(vm) Factory: Disconnected';
        vscode.window.showErrorMessage(`Failed to connect: ${error}`);
    }
}

/**
 * Submit the currently selected editor text as an AI generation story.
 *
 * Uses ``Date.now()`` as a simple ``interaction_id``.  In production, a UUID
 * should be used to guarantee global uniqueness across service instances.
 *
 * Falls back to the literal string ``'default task'`` when no text is selected.
 */
async function runFactoryTask() {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        vscode.window.showWarningMessage('No active editor');
        return;
    }
    
    const selection = editor.selection;
    const selectedText = editor.document.getText(selection);
    
    const interactionId = Date.now().toString();
    
    const response = await fetch(`${backendUrl}/v1/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            company_id: 1,
            story: selectedText || 'default task',
            interaction_id: interactionId
        })
    });
    
    if (response.ok) {
        vscode.window.showInformationMessage(`Task started: ${interactionId}`);
    } else {
        vscode.window.showErrorMessage('Failed to start task');
    }
}

/**
 * Prompt for a search query and display matching Engram results in a QuickPick.
 *
 * Shows the first 100 characters of each result's ``content`` field as the
 * detail line so the user can quickly identify relevant memories.
 */
async function showMemorySearch() {
    const query = await vscode.window.showInputBox({
        prompt: 'Enter search query for memory',
        placeHolder: 'Search memory...'
    });
    
    if (!query) return;
    
    try {
        const response = await fetch(`${backendUrl}/v1/memory/search?query=${encodeURIComponent(query)}`);
        const data = await response.json();
        
        const results = data.results || [];
        if (results.length === 0) {
            vscode.window.showInformationMessage('No memory results found');
            return;
        }
        
        const items = results.map((r: any, i: number) => ({
            label: `Result ${i + 1}`,
            detail: r.content?.substring(0, 100)
        }));
        
        vscode.window.showQuickPick(items);
    } catch (error) {
        vscode.window.showErrorMessage(`Search failed: ${error}`);
    }
}

/**
 * Prompt for a sprint name and create a new 14-day sprint via the REST API.
 *
 * Hard-codes ``company_id: 1`` and ``backlog_items: []`` — future versions
 * should derive these from workspace configuration or user input.
 */
async function startSprint() {
    const sprintName = await vscode.window.showInputBox({
        prompt: 'Enter sprint name',
        placeHolder: 'Sprint 1'
    });
    
    if (!sprintName) return;
    
    const startDate = new Date().toISOString().split('T')[0];
    const durationDays = 14;
    
    try {
        const response = await fetch(`${backendUrl}/v1/start_sprint`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                company_id: 1,
                sprint_name: sprintName,
                start_date: startDate,
                duration_days: durationDays,
                backlog_items: []
            })
        });
        
        const data = await response.json();
        vscode.window.showInformationMessage(`Sprint started: ${data.sprint_id}`);
    } catch (error) {
        vscode.window.showErrorMessage(`Failed to start sprint: ${error}`);
    }
}

/**
 * Open a terminal that streams real-time Glassroom events via ``curl -N`` (SSE).
 *
 * The ``-N`` flag disables curl's output buffering so each SSE event is printed
 * immediately as it arrives from the backend.
 */
async function streamGlassroom() {
    const sprintId = await vscode.window.showInputBox({
        prompt: 'Enter sprint ID to stream',
        placeHolder: '1'
    });
    
    if (!sprintId) return;
    
    const terminal = vscode.window.createTerminal('Factory Glassroom');
    terminal.sendText(`curl -N ${backendUrl}/v1/glassroom/stream?sprint_id=${sprintId}`);
    terminal.show();
}

/**
 * Extension deactivation hook — called when VS Code unloads the extension.
 *
 * Closes any open EventSource connection to release network resources cleanly.
 */
export function deactivate() {
    if (eventSource) {
        eventSource.close();
    }
}
