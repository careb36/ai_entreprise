/**
 * Fabrique Factory — VS Code Extension entry point.
 *
 * Exposes six commands that connect the VS Code editor to the Fabrique backend:
 *
 * | Command                      | Description                                                        |
 * |------------------------------|--------------------------------------------------------------------|
 * | `factory.connect`            | Health-check the backend and update the status bar.               |
 * | `factory.runTask`            | Submit the selected text as an AI generation story.               |
 * | `factory.showMemory`         | Keyword search over episodic Engram memory.                        |
 * | `factory.startSprint`        | Create a new 2-week sprint via the REST API.                       |
 * | `factory.streamGlassroom`    | Open an integrated terminal streaming Glassroom SSE (legacy curl). |
 * | `factory.glassroomPanel`     | Open a native WebView panel streaming Glassroom events via SSE.   |
 *
 * The status bar item (bottom-left) reflects the current connection state.
 */

import * as vscode from 'vscode';

/** Shared status bar item showing Factory connection state. */
let statusBarItem: vscode.StatusBarItem;

/** Base URL of the Fabrique backend REST API. */
let backendUrl: string;

/** Singleton GlassRoom WebView panel (one per VS Code window). */
let glassroomPanel: vscode.WebviewPanel | undefined;

/**
 * Extension activation entry point — called once by VS Code when the extension loads.
 *
 * Reads the ``factory.backendUrl`` setting, creates the status bar item, and
 * registers all six commands.
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

    context.subscriptions.push(
        vscode.commands.registerCommand('factory.glassroomPanel', async () => {
            await openGlassroomPanel(context);
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
 *
 * @deprecated Use ``openGlassroomPanel`` for a richer native VS Code experience.
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

// ---------------------------------------------------------------------------
// GlassRoom WebView panel
// ---------------------------------------------------------------------------

/**
 * Open (or reveal) a native VS Code WebView panel that streams real-time
 * GlassRoom events from the backend SSE endpoint.
 *
 * The WebView hosts a self-contained HTML page that:
 * - Connects to ``GET /v1/glassroom/stream?sprint_id=<id>`` via the browser's
 *   built-in ``EventSource`` API (supported natively inside VS Code WebViews).
 * - Renders each incoming event as a colour-coded card (one per agent role).
 * - Auto-scrolls to the latest message, with a pause-scroll toggle button.
 * - Reconnects automatically if the SSE connection drops.
 *
 * The panel is a singleton: calling the command a second time with the same
 * sprint ID brings the existing panel to the foreground instead of opening
 * a duplicate.
 *
 * @param context Extension context used to construct resource URIs for the panel.
 */
async function openGlassroomPanel(context: vscode.ExtensionContext) {
    const sprintId = await vscode.window.showInputBox({
        prompt: 'Enter sprint ID to monitor',
        placeHolder: '1',
        value: '1',
    });

    if (!sprintId) return;

    // Reuse the existing panel if it's already open.
    if (glassroomPanel) {
        glassroomPanel.reveal(vscode.ViewColumn.Two);
        glassroomPanel.webview.postMessage({ type: 'reconnect', sprintId });
        return;
    }

    glassroomPanel = vscode.window.createWebviewPanel(
        'factoryGlassroom',
        `Factory GlassRoom — Sprint ${sprintId}`,
        vscode.ViewColumn.Two,
        {
            enableScripts: true,
            // Allow the WebView to fetch from the backend URL.
            localResourceRoots: [],
        }
    );

    glassroomPanel.webview.html = _buildGlassroomHtml(backendUrl, sprintId);

    // Clean up the singleton reference when the user closes the panel.
    glassroomPanel.onDidDispose(
        () => { glassroomPanel = undefined; },
        null,
        context.subscriptions,
    );
}

/**
 * Build the self-contained HTML page for the GlassRoom WebView panel.
 *
 * The page uses vanilla JS (no bundler required) and the browser ``EventSource``
 * API to consume the backend SSE stream.  Each event is rendered as a card
 * colour-coded by agent role using a fixed palette.
 *
 * @param apiBase  Backend base URL (e.g. ``"http://localhost:8000"``).
 * @param sprintId Sprint identifier used as the ``sprint_id`` query parameter.
 */
function _buildGlassroomHtml(apiBase: string, sprintId: string): string {
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Factory GlassRoom</title>
  <style>
    :root {
      --bg: #1e1e2e;
      --surface: #2a2a3e;
      --border: #3a3a5e;
      --text: #cdd6f4;
      --muted: #888bac;
      --accent: #89b4fa;
      --success: #a6e3a1;
      --warn: #f9e2af;
      --error: #f38ba8;
      --info: #89dceb;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'Segoe UI', system-ui, sans-serif;
      font-size: 13px;
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
    }
    header {
      padding: 10px 16px;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      gap: 12px;
      flex-shrink: 0;
    }
    h1 { font-size: 14px; font-weight: 600; color: var(--accent); flex: 1; }
    #status-dot {
      width: 10px; height: 10px; border-radius: 50%;
      background: var(--muted); flex-shrink: 0;
      transition: background 0.3s;
    }
    #status-dot.connected  { background: var(--success); }
    #status-dot.error      { background: var(--error); }
    #status-text { color: var(--muted); font-size: 12px; }
    button {
      background: var(--surface);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 4px 10px;
      cursor: pointer;
      font-size: 12px;
    }
    button:hover { background: var(--border); }
    #log {
      flex: 1;
      overflow-y: auto;
      padding: 12px 16px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .event-card {
      background: var(--surface);
      border-left: 3px solid var(--accent);
      border-radius: 4px;
      padding: 8px 12px;
      animation: fadeIn 0.2s ease;
    }
    .event-card.status      { border-color: var(--muted); }
    .event-card.agent_complete { border-color: var(--success); }
    .event-card.error       { border-color: var(--error); }
    .event-card.hitl        { border-color: var(--warn); }
    .event-card.connected   { border-color: var(--info); }
    .event-meta {
      display: flex; gap: 10px; align-items: baseline;
      margin-bottom: 4px;
    }
    .agent-badge {
      font-size: 11px; font-weight: 700;
      background: var(--border);
      padding: 1px 6px; border-radius: 10px;
      color: var(--accent);
    }
    .event-time { color: var(--muted); font-size: 11px; }
    .event-type { color: var(--muted); font-size: 11px; font-style: italic; }
    .event-msg  { line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; } }
    #empty-state {
      text-align: center; color: var(--muted); margin-top: 60px;
    }
  </style>
</head>
<body>
  <header>
    <div id="status-dot"></div>
    <h1>GlassRoom — Sprint ${sprintId}</h1>
    <span id="status-text">Connecting…</span>
    <button id="clear-btn">Clear</button>
    <button id="pause-btn">Pause</button>
  </header>
  <div id="log">
    <div id="empty-state">Waiting for agent events…</div>
  </div>

  <script>
    const API_BASE   = ${JSON.stringify(apiBase)};
    const SPRINT_ID  = ${JSON.stringify(sprintId)};
    const log        = document.getElementById('log');
    const dot        = document.getElementById('status-dot');
    const statusText = document.getElementById('status-text');
    const pauseBtn   = document.getElementById('pause-btn');
    const clearBtn   = document.getElementById('clear-btn');

    let paused   = false;
    let es       = null;
    let eventCount = 0;

    // ── Colour palette per agent role ─────────────────────────────────────
    const ROLE_COLORS = {
      PO: '#cba6f7', Architect: '#89b4fa', Developer: '#a6e3a1',
      QA: '#f9e2af', ScrumMaster: '#89dceb', DevOps: '#fab387',
      Security: '#f38ba8', System: '#6c7086',
    };

    function agentColor(agent) {
      return ROLE_COLORS[agent] || '#cdd6f4';
    }

    // ── Render an event card ───────────────────────────────────────────────
    function appendEvent(data) {
      const empty = document.getElementById('empty-state');
      if (empty) empty.remove();

      eventCount++;
      const card  = document.createElement('div');
      const type  = (data.type || 'message').toLowerCase();
      const agent = data.agent || data.role || 'System';
      const msg   = data.message || data.content || JSON.stringify(data);
      const time  = new Date().toLocaleTimeString();

      card.className = 'event-card ' + (
        type.includes('complete') ? 'agent_complete' :
        type === 'status'         ? 'status' :
        type === 'error'          ? 'error' :
        type.includes('hitl')     ? 'hitl' :
        type === 'connected'      ? 'connected' : ''
      );

      const color = agentColor(agent);
      card.innerHTML =
        '<div class="event-meta">' +
          '<span class="agent-badge" style="color:' + color + '">' + escHtml(agent) + '</span>' +
          '<span class="event-type">' + escHtml(type) + '</span>' +
          '<span class="event-time">' + time + '</span>' +
        '</div>' +
        '<div class="event-msg">' + escHtml(msg) + '</div>';

      log.appendChild(card);
      if (!paused) card.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }

    function escHtml(s) {
      return String(s)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    // ── SSE connection ─────────────────────────────────────────────────────
    function connect() {
      if (es) { es.close(); }

      const url = API_BASE + '/v1/glassroom/stream?sprint_id=' + encodeURIComponent(SPRINT_ID);
      es = new EventSource(url);

      es.onopen = () => {
        dot.className = 'connected';
        statusText.textContent = 'Connected';
      };

      es.onmessage = (evt) => {
        try {
          const data = JSON.parse(evt.data);
          appendEvent(data);
        } catch (_) {
          appendEvent({ type: 'raw', agent: 'Stream', message: evt.data });
        }
      };

      es.onerror = () => {
        dot.className = 'error';
        statusText.textContent = 'Reconnecting…';
        // EventSource reconnects automatically; just update the UI.
      };
    }

    // ── Controls ───────────────────────────────────────────────────────────
    pauseBtn.addEventListener('click', () => {
      paused = !paused;
      pauseBtn.textContent = paused ? 'Resume' : 'Pause';
    });

    clearBtn.addEventListener('click', () => {
      log.innerHTML = '';
      eventCount = 0;
    });

    // ── VS Code → WebView messages (e.g. reconnect to a new sprint) ────────
    window.addEventListener('message', (evt) => {
      const msg = evt.data;
      if (msg && msg.type === 'reconnect') {
        connect();
      }
    });

    connect();
  </script>
</body>
</html>`;
}

/**
 * Extension deactivation hook — called when VS Code unloads the extension.
 *
 * Disposes the GlassRoom WebView panel if it is still open, which closes
 * the underlying SSE connection cleanly.
 */
export function deactivate() {
    if (glassroomPanel) {
        glassroomPanel.dispose();
    }
}
