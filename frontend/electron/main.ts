import { app, BrowserWindow, Menu, dialog, shell, type WebContents } from 'electron'
import * as path from 'path'
import { backendUrl, isWebUrl, setupIpcHandlers } from './ipc-handlers'
import { startLocalServer, type LocalServer } from './local-server'
import { mainWindowNavigation, newWindowRequest, ssoWindowNavigation, type Decision } from './navigation'

// `npm run electron:dev` passes --dev and serves the renderer from Vite;
// otherwise the built renderer is served by the local server
// (local-server.ts), so the page and the API share one origin and the
// session cookies are first-party (issue #524, docs/design/SESSIONS.md,
// "Desktop app").
const isDev = process.argv.includes('--dev') || process.env.NODE_ENV === 'development'
const DEV_SERVER_URL = process.env.ELECTRON_DEV_URL || 'http://localhost:5173'

let mainWindow: BrowserWindow | null = null
let ssoWindow: BrowserWindow | null = null
let localServer: LocalServer | null = null

/** The origin the app's pages are served from. */
function appOrigin(): string {
  return isDev ? new URL(DEV_SERVER_URL).origin : (localServer?.origin ?? '')
}

/** Act on navigation decisions (navigation.ts) for *contents*. */
function guard(contents: WebContents, decide: (url: string) => Decision) {
  const onNavigate = (event: Electron.Event, url: string) => {
    const decision = decide(url)
    if (decision.kind === 'allow') return
    event.preventDefault()
    if (decision.kind === 'external') void shell.openExternal(decision.url)
    else if (decision.kind === 'sso') openSsoWindow(decision.url)
    else if (decision.kind === 'load') void contents.loadURL(decision.url)
    else if (decision.kind === 'finish') finishSso(decision.url)
  }
  contents.on('will-navigate', onNavigate)
  contents.on('will-redirect', onNavigate)
  contents.setWindowOpenHandler(({ url }) => {
    // Links with target="_blank" (a repository's GitHub page) open in the
    // user's browser, never in a new Electron window.
    const decision = newWindowRequest(url, appOrigin())
    if (decision.kind === 'external') void shell.openExternal(decision.url)
    return { action: 'deny' }
  })
}

/** Single sign-on in its own window, without the preload script, since it
 * shows the identity provider's pages (navigation.ts). */
function openSsoWindow(url: string) {
  ssoWindow?.close()
  ssoWindow = new BrowserWindow({
    width: 520,
    height: 720,
    parent: mainWindow ?? undefined,
    modal: mainWindow !== null,
    title: 'Fabrique',
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  })
  guard(ssoWindow.webContents, target => ssoWindowNavigation(target, appOrigin()))
  ssoWindow.on('closed', () => {
    ssoWindow = null
  })
  void ssoWindow.loadURL(url)
}

function finishSso(url: string) {
  ssoWindow?.close()
  void mainWindow?.loadURL(url)
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      // Compiled next to this file (dist-electron-main/preload.js).
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
    title: 'Fabrique'
  })

  if (isDev) {
    // The Vite dev server is trusted and talks to the backend on localhost
    // (same site, so the cookies are sent); links with target="_blank" open
    // in the user's browser.
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
      if (isWebUrl(url)) void shell.openExternal(url)
      return { action: 'deny' }
    })
    // The Vite dev server may still be starting when Electron comes up.
    mainWindow.webContents.on('did-fail-load', (_event, _code, _description, url) => {
      if (url.startsWith(DEV_SERVER_URL)) setTimeout(() => mainWindow?.loadURL(DEV_SERVER_URL), 1000)
    })
    mainWindow.loadURL(DEV_SERVER_URL)
    mainWindow.webContents.openDevTools()
  } else {
    // The main window stays on the local origin; single sign-on gets its own
    // window; any other web link opens in the user's browser.
    guard(mainWindow.webContents, url => mainWindowNavigation(url, appOrigin()))
    mainWindow.loadURL(`${appOrigin()}/`)
  }

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

function navigate(route: string) {
  mainWindow?.webContents.send('navigate', route)
}

function createMenu() {
  const template: Electron.MenuItemConstructorOptions[] = [
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' as const }] : []),
    {
      label: 'File',
      submenu: [
        process.platform === 'darwin' ? { role: 'close' } : { role: 'quit' }
      ]
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' }
      ]
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' }
      ]
    },
    {
      label: 'Factory',
      submenu: [
        {
          label: 'Evidence',
          click: () => navigate('/evidence')
        },
        {
          label: 'Sprints',
          click: () => navigate('/sprints')
        },
        {
          label: 'New Company',
          click: () => navigate('/companies/new')
        }
      ]
    },
    {
      label: 'Help',
      submenu: [
        {
          label: 'About Fabrique',
          click: () => {
            void dialog.showMessageBox({
              type: 'info',
              title: 'About Fabrique',
              message: `Fabrique v${app.getVersion()}`,
              detail: 'Factory system with LangGraph orchestration'
            })
          }
        }
      ]
    }
  ]

  const menu = Menu.buildFromTemplate(template)
  Menu.setApplicationMenu(menu)
}

app.whenReady().then(async () => {
  if (!isDev) {
    try {
      // Relative to dist-electron-main/: the renderer build lives in dist/.
      localServer = await startLocalServer({ distDir: path.join(__dirname, '../dist'), backendUrl: backendUrl() })
    } catch (error) {
      dialog.showErrorBox('Fabrique', `The local server could not start: ${String(error)}`)
      app.quit()
      return
    }
  }
  setupIpcHandlers(appOrigin)
  createWindow()
  createMenu()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow()
    }
  })
})

app.on('will-quit', () => {
  void localServer?.close()
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
