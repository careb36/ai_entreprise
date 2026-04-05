import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  navigate: (path: string) => ipcRenderer.send('navigate', path),
  onNavigate: (callback: (path: string) => void) => {
    ipcRenderer.on('navigate', (_event, path) => callback(path))
  },
  platform: process.platform,
  versions: {
    node: process.versions.node,
    chrome: process.versions.chrome,
    electron: process.versions.electron
  }
})
