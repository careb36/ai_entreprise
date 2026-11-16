/** What the wizard's tests share: the daemon's host on a Mac and a folder it inspected (`CompanyNew*.test.tsx`). */
import type { WorkspaceInspection } from '../services/api'

export const MAC_HOST = { hostname: 'ana-mbp', platform: 'darwin', home: '/Users/ana', sep: '/' }

export function inspection(path: string, overrides: Partial<WorkspaceInspection> = {}): WorkspaceInspection {
  return {
    host: MAC_HOST,
    path,
    name: path.split('/').pop() || path,
    exists: true,
    is_dir: true,
    git: { is_repo: false, branch: null, remote_url: null, provider: null, web_url: null },
    ...overrides,
  }
}
