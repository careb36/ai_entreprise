import * as vscode from 'vscode';

let statusBarItem: vscode.StatusBarItem;
let backendUrl: string;
let eventSource: EventSource | null;

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

export function deactivate() {
    if (eventSource) {
        eventSource.close();
    }
}
