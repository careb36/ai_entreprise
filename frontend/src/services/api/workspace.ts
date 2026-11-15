import { fetchApi } from './client'

// ── Workspace daemon (host-native project folders) ───────────────────────────
// Paths are host-native absolute strings (`/Users/ana/code`, `C:\Users\ana`).
// Treat them as opaque: never join or normalise them, always reuse the `path`
// the API returned.

/** `darwin`, `linux`, `win32`, or another Node platform string. */
export type WorkspacePlatform = 'darwin' | 'linux' | 'win32' | (string & {})

export interface WorkspaceHost {
  hostname: string
  platform: WorkspacePlatform
  home: string
  sep: string
}

export interface WorkspaceStatus {
  connected: boolean
  /** connected, absent (nothing listens), unknown (the backend cannot tell) or incompatible
   * (it answers with a protocol this backend does not speak); older backends omit it. */
  state?: 'connected' | 'absent' | 'unknown' | 'incompatible'
  /** Why the state is absent, unknown or incompatible. */
  reason?: string | null
  host: WorkspaceHost | null
  jetstream?: boolean
}

export interface WorkspaceLocation {
  name: string
  path: string
}

export interface WorkspaceEntry extends WorkspaceLocation {
  is_git: boolean
}

export interface WorkspaceBrowseResponse {
  host: WorkspaceHost
  path: string
  parent: string | null
  entries: WorkspaceEntry[]
  shortcuts: WorkspaceLocation[]
  roots: WorkspaceLocation[]
  truncated: boolean
}

export type GitProvider = 'github' | 'gitlab' | 'bitbucket' | 'other'

export interface WorkspaceGitInfo {
  is_repo: boolean
  branch: string | null
  remote_url: string | null
  provider: GitProvider | null
  web_url: string | null
}

export interface WorkspaceInspection {
  host: WorkspaceHost
  path: string
  name: string
  exists: boolean
  is_dir: boolean
  git: WorkspaceGitInfo | null
}

export const workspaceEndpoints = () => ({
  // Workspace daemon. A 503 means "daemon not connected": do not retry it.
  workspaceStatus: () =>
    fetchApi<WorkspaceStatus>('/v1/workspace/status', { method: 'GET', retryServerErrors: false }),

  browseWorkspace: (path?: string) =>
    fetchApi<WorkspaceBrowseResponse>('/v1/workspace/browse', {
      method: 'GET',
      params: path ? { path } : undefined,
      retryServerErrors: false,
    }),

  inspectWorkspace: (path: string) =>
    fetchApi<WorkspaceInspection>('/v1/workspace/inspect', {
      method: 'GET',
      params: { path },
      retryServerErrors: false,
    }),
})
