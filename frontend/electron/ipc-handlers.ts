import { ipcMain } from 'electron'

export function setupIpcHandlers() {
  ipcMain.handle('get-backend-url', () => {
    return process.env.BACKEND_URL || 'http://localhost:8000'
  })

  ipcMain.handle('open-external', async (_event, url: string) => {
    const { shell } = await import('electron')
    return shell.openExternal(url)
  })

  ipcMain.handle('get-app-version', () => {
    return '1.0.0'
  })
}
