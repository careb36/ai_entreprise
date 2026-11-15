import { app, BrowserWindow, dialog, ipcMain, shell, type IpcMainInvokeEvent } from 'electron'

/** Opens only web links in the user's browser, never file:// or custom schemes. */
export function isWebUrl(url: string): boolean {
  try {
    const { protocol } = new URL(url)
    return protocol === 'https:' || protocol === 'http:'
  } catch {
    return false
  }
}

/** The backend the app talks to; the packaged app's local server forwards `/v1/*` there. */
export function backendUrl(): string {
  return process.env.BACKEND_URL || 'http://localhost:8000'
}

/** Whether *event* comes from a page on the app's own origin (not, say, an
 * identity provider's page; the single sign-on window has no preload, so
 * this is a second line). */
export function fromOrigin(event: Pick<IpcMainInvokeEvent, 'senderFrame'>, origin: string): boolean {
  const url = event.senderFrame?.url
  if (!url || !origin) return false
  try {
    return new URL(url).origin === origin
  } catch {
    return false
  }
}

export function setupIpcHandlers(appOrigin: () => string) {
  const handle = (channel: string, listener: (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown) => {
    ipcMain.handle(channel, (event, ...args) => {
      if (!fromOrigin(event, appOrigin())) throw new Error('Refused: not the app page')
      return listener(event, ...args)
    })
  }

  handle('get-backend-url', () => backendUrl())

  handle('open-external', async (_event, url: unknown) => {
    if (typeof url !== 'string' || !isWebUrl(url)) throw new Error('Only http(s) URLs can be opened')
    await shell.openExternal(url)
  })

  handle('get-app-version', () => app.getVersion())

  // Native folder dialog for choosing the project folder; resolves to the
  // chosen absolute path, or null when the user cancels.
  handle('select-directory', async event => {
    const options: Electron.OpenDialogOptions = { properties: ['openDirectory'] }
    const window = BrowserWindow.fromWebContents(event.sender)
    const result = window
      ? await dialog.showOpenDialog(window, options)
      : await dialog.showOpenDialog(options)
    if (result.canceled || result.filePaths.length === 0) return null
    return result.filePaths[0]
  })
}
