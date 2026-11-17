// The wizard's project folder step: the daemon's state, the folder picker and
// the native dialog, the inspection and the Git workflow it pre-sets. The rest
// of the wizard is in CompanyNew.test.tsx; the shared host and inspection are
// in test/companyNewFixtures.ts.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider, useMutation } from '@tanstack/react-query'
import CompanyNew from './CompanyNew'
import { api, ApiError, type WorkspaceInspection, type WorkspaceStatus } from '../services/api'
import { useAuthStore } from '../store/useAuthStore'
import { inspection, MAC_HOST } from '../test/companyNewFixtures'

const modelCatalogue = vi.hoisted(() => ({
  models: [] as { id: string; provider: string; tier: string; description: string }[],
}))

const daemon = vi.hoisted(() => ({
  status: {
    data: undefined as WorkspaceStatus | undefined,
    isPending: false,
    error: null as unknown,
  },
}))

vi.mock('../hooks/useQueries', () => ({
  useModels: () => ({ data: modelCatalogue }),
  useDiscoverTeam: () => useMutation({
    mutationFn: ({ companyId, projectPath }: { companyId: number | null; projectPath: string }) =>
      api.discoverTeam(companyId, projectPath),
  }),
  useInitCompany: () => useMutation({ mutationFn: api.initCompany }),
  useWorkspaceStatus: () => daemon.status,
}))

describe('project folder on the user\'s computer', () => {
  const listing = {
    host: MAC_HOST,
    path: '/Users/ana/code',
    parent: '/Users/ana',
    entries: [{ name: 'app-link', path: '/Users/ana/code/app-link', is_git: true }],
    shortcuts: [{ name: 'Home', path: '/Users/ana' }],
    roots: [{ name: '/', path: '/' }],
    truncated: false,
  }
  const githubProject = inspection('/Users/ana/code/la-fabrique', {
    name: 'la-fabrique',
    git: {
      is_repo: true,
      branch: 'main',
      remote_url: 'git@github.com:careb36/la-fabrique.git',
      provider: 'github',
      web_url: 'https://github.com/careb36/la-fabrique',
    },
  })

  let client: QueryClient

  beforeEach(() => {
    useAuthStore.setState({ expiresAt: Date.now() + 60_000, meLoaded: true })
    daemon.status = { data: { connected: true, host: MAC_HOST, jetstream: true }, isPending: false, error: null }
    client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  })

  afterEach(() => {
    client.clear()
    delete window.electronAPI
    vi.restoreAllMocks()
  })

  function renderWizard() {
    return render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <CompanyNew />
        </MemoryRouter>
      </QueryClientProvider>,
    )
  }

  it('starts with an empty field and a placeholder for the host platform', () => {
    vi.spyOn(api, 'inspectWorkspace')
    renderWizard()
    const input = screen.getByLabelText('Project folder')
    expect(input).toHaveValue('')
    expect(input).toHaveAttribute('placeholder', '~/code/my-app')
    expect(screen.getByRole('button', { name: 'Scan' })).toBeDisabled()
    expect(screen.getByText('Workspace daemon connected on ana-mbp (macOS)')).toBeInTheDocument()
  })

  it('says it cannot tell when the daemon state is unknown, and shows how to start it only when absent', () => {
    daemon.status = {
      data: { connected: false, state: 'unknown', reason: 'no answer within 3 s', host: null, jetstream: true },
      isPending: false,
      error: null,
    }
    const { unmount } = renderWizard()
    expect(screen.getByText(/Cannot tell whether the workspace daemon is running: no answer within 3 s/)).toBeInTheDocument()
    expect(screen.queryByText('./scripts/start-daemon.sh')).not.toBeInTheDocument()
    unmount()

    daemon.status = {
      data: { connected: false, state: 'absent', reason: 'no daemon is listening', host: null, jetstream: true },
      isPending: false,
      error: null,
    }
    renderWizard()
    expect(screen.queryByText(/Cannot tell whether/)).not.toBeInTheDocument()
    expect(screen.getByText('./scripts/start-daemon.sh')).toBeInTheDocument()
  })

  it('names the protocol problem when the daemon answers but is incompatible, without the start instructions', () => {
    daemon.status = {
      data: {
        connected: false,
        state: 'incompatible',
        reason: 'the workspace daemon speaks protocol 2; this backend supports 1: update the backend',
        host: { hostname: 'ana-mbp', platform: 'darwin', home: '/Users/ana', sep: '/' },
        jetstream: true,
      },
      isPending: false,
      error: null,
    }
    renderWizard()
    expect(
      screen.getByText(/The workspace daemon on ana-mbp answers, but cannot be used: the workspace daemon speaks protocol 2/),
    ).toBeInTheDocument()
    expect(screen.queryByText('./scripts/start-daemon.sh')).not.toBeInTheDocument()
  })

  it('inspects the folder chosen in the picker and shows the canonical path, provider and branch', async () => {
    const browse = vi.spyOn(api, 'browseWorkspace').mockResolvedValue(listing)
    const inspect = vi.spyOn(api, 'inspectWorkspace').mockResolvedValue(githubProject)
    renderWizard()

    fireEvent.click(screen.getByRole('button', { name: /Browse/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Choose a project folder' })
    expect(browse).toHaveBeenCalledWith(undefined)
    await within(dialog).findByText('app-link')
    browse.mockResolvedValue({ ...listing, path: '/Users/ana/code/app-link', parent: '/Users/ana/code', entries: [] })
    fireEvent.click(within(dialog).getByRole('button', { name: /app-link/ }))
    expect(browse).toHaveBeenLastCalledWith('/Users/ana/code/app-link')
    await within(dialog).findByText('/Users/ana/code/app-link')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Use this folder' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(inspect).toHaveBeenCalledWith('/Users/ana/code/app-link')
    const card = await screen.findByTestId('project-card')
    expect(within(card).getByText('la-fabrique')).toBeInTheDocument()
    expect(within(card).getByTestId('project-provider')).toHaveTextContent('GitHub')
    expect(within(card).getByText('main')).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: 'https://github.com/careb36/la-fabrique' }))
      .toHaveAttribute('href', 'https://github.com/careb36/la-fabrique')
    // The field now holds the canonical path the daemon resolved, not the symlink chosen.
    expect(screen.getByLabelText('Project folder')).toHaveValue('/Users/ana/code/la-fabrique')
    expect(screen.getByRole('button', { name: 'Scan' })).toBeEnabled()
  })

  it('says when a folder is not a git repository', async () => {
    vi.spyOn(api, 'inspectWorkspace').mockResolvedValue(inspection('/Users/ana/notes', { name: 'notes' }))
    renderWizard()
    const input = screen.getByLabelText('Project folder')
    fireEvent.change(input, { target: { value: '/Users/ana/notes' } })
    fireEvent.blur(input)
    expect(await screen.findByTestId('project-provider')).toHaveTextContent('Not a git repository')
    expect(screen.getByText(/not a git clone/)).toBeInTheDocument()
  })

  it('shows an error and keeps Scan disabled when the folder does not exist', async () => {
    const discover = vi.spyOn(api, 'discoverTeam')
    vi.spyOn(api, 'inspectWorkspace').mockResolvedValue(
      inspection('/Users/ana/code/missing', { exists: false, is_dir: false, git: null }))
    renderWizard()
    const input = screen.getByLabelText('Project folder')
    fireEvent.change(input, { target: { value: '~/code/missing' } })
    fireEvent.blur(input)

    expect(await screen.findByText('This folder does not exist on ana-mbp: /Users/ana/code/missing'))
      .toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Scan' })).toBeDisabled()
    fireEvent.keyDown(input, { key: 'Enter' })
    await act(async () => {})
    expect(discover).not.toHaveBeenCalled()
    expect(screen.queryByTestId('project-card')).not.toBeInTheDocument()
  })

  it('opens the native dialog in the desktop app instead of the daemon picker', async () => {
    const selectDirectory = vi.fn().mockResolvedValue('/Users/ana/code/la-fabrique')
    window.electronAPI = {
      platform: 'darwin',
      versions: { node: '', chrome: '', electron: '' },
      selectDirectory,
      onNavigate: () => () => {},
      getBackendUrl: async () => '',
      getAppVersion: async () => '',
      openExternal: async () => {},
    }
    const browse = vi.spyOn(api, 'browseWorkspace').mockResolvedValue(listing)
    const inspect = vi.spyOn(api, 'inspectWorkspace').mockResolvedValue(githubProject)
    renderWizard()

    fireEvent.click(screen.getByRole('button', { name: /Browse$/ }))
    await waitFor(() => expect(inspect).toHaveBeenCalledWith('/Users/ana/code/la-fabrique'))
    expect(selectDirectory).toHaveBeenCalledOnce()
    expect(browse).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(await screen.findByTestId('project-card')).toBeInTheDocument()

    // The daemon picker stays available as a secondary action.
    fireEvent.click(screen.getByRole('button', { name: 'Browse via the workspace daemon' }))
    expect(await screen.findByRole('dialog', { name: 'Choose a project folder' })).toBeInTheDocument()
    expect(browse).toHaveBeenCalledWith('/Users/ana/code/la-fabrique')
  })

  it('does nothing when the native dialog is cancelled', async () => {
    window.electronAPI = {
      platform: 'win32',
      versions: { node: '', chrome: '', electron: '' },
      selectDirectory: vi.fn().mockResolvedValue(null),
      onNavigate: () => () => {},
      getBackendUrl: async () => '',
      getAppVersion: async () => '',
      openExternal: async () => {},
    }
    const inspect = vi.spyOn(api, 'inspectWorkspace')
    renderWizard()
    fireEvent.click(screen.getByRole('button', { name: /Browse$/ }))
    await waitFor(() => expect(window.electronAPI!.selectDirectory).toHaveBeenCalledOnce())
    expect(inspect).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Project folder')).toHaveValue('')
  })

  it('lets a typed path be scanned when the daemon is down, and sends the canonical discovery path to init_company', async () => {
    daemon.status = { data: { connected: false, host: null }, isPending: false, error: null }
    vi.spyOn(api, 'inspectWorkspace').mockRejectedValue(
      new ApiError(503, 'Service is starting up.', 'Workspace daemon not connected'))
    const discover = vi.spyOn(api, 'discoverTeam').mockResolvedValue({
      discovery_id: 3,
      project_path: 'C:\\Users\\ana\\source\\repos\\la-fabrique',
      project: null,
      detected_stack: {
        name: 'la-fabrique', language: 'TypeScript', frameworks: [], databases: [], infra: [],
        complexity: 'Low', file_count: 2,
      },
      suggested_team: [],
    })
    const init = vi.spyOn(api, 'initCompany').mockImplementation(() => new Promise(() => {}))
    renderWizard()

    const input = screen.getByLabelText('Project folder')
    fireEvent.change(input, { target: { value: 'c:\\users\\ana\\source\\repos\\la-fabrique' } })
    fireEvent.click(screen.getByRole('button', { name: 'Scan' }))

    await screen.findByText(/Stack detected/)
    expect(discover).toHaveBeenCalledWith(null, 'c:\\users\\ana\\source\\repos\\la-fabrique')
    expect(screen.getAllByText('The workspace daemon is not connected.').length).toBeGreaterThan(0)
    expect(screen.getByText('You can still type the folder path and scan it.')).toBeInTheDocument()
    expect(input).toHaveValue('C:\\Users\\ana\\source\\repos\\la-fabrique')

    for (let step = 0; step < 5; step++) fireEvent.click(screen.getByRole('button', { name: 'Next →' }))
    fireEvent.click(screen.getByRole('button', { name: /Initialize Factory/ }))
    await waitFor(() => expect(init).toHaveBeenCalledOnce())
    expect(init.mock.calls[0][0].project_path).toBe('C:\\Users\\ana\\source\\repos\\la-fabrique')
    // Nothing observed the folder: the Git workflow is not asked for.
    expect(init.mock.calls[0][0].git_workflow).toBe(false)
  })

  it('pre-sets the Git workflow and its base branch from the folder the daemon inspected', async () => {
    const project: WorkspaceInspection = {
      host: { hostname: 'laptop', platform: 'linux', home: '/home/ana' } as WorkspaceInspection['host'],
      path: '/home/ana/app', name: 'app', exists: true, is_dir: true,
      git: { is_repo: true, branch: 'main', remote_url: null, provider: null, web_url: null },
    }
    vi.spyOn(api, 'inspectWorkspace').mockResolvedValue(project)
    vi.spyOn(api, 'discoverTeam').mockResolvedValue({
      discovery_id: 4, project_path: '/home/ana/app', project,
      detected_stack: { name: 'app', language: 'Python', frameworks: [], databases: [], infra: [], complexity: 'Low', file_count: 2 },
      suggested_team: [],
    })
    const init = vi.spyOn(api, 'initCompany').mockImplementation(() => new Promise(() => {}))
    renderWizard()

    fireEvent.change(screen.getByLabelText('Project folder'), { target: { value: '/home/ana/app' } })
    fireEvent.click(screen.getByRole('button', { name: 'Scan' }))
    await screen.findByText(/Stack detected/)
    for (let step = 0; step < 5; step++) fireEvent.click(screen.getByRole('button', { name: 'Next →' }))

    expect(screen.getByRole('checkbox', { name: /Git branch per task/ })).toBeChecked()
    expect(screen.getByLabelText('Base branch task branches start from')).toHaveValue('main')
    fireEvent.click(screen.getByRole('button', { name: /Initialize Factory/ }))
    await waitFor(() => expect(init).toHaveBeenCalledOnce())
    expect(init.mock.calls[0][0]).toMatchObject({ git_workflow: true, git_base_branch: 'main', doc_language: 'en' })
  })

  it('starts the Git choice again from the new folder when the person goes back and picks another', async () => {
    const folder = (path: string, branch: string): WorkspaceInspection => ({
      host: { hostname: 'laptop', platform: 'linux', home: '/home/ana' } as WorkspaceInspection['host'],
      path, name: path.split('/').pop() ?? path, exists: true, is_dir: true,
      git: { is_repo: true, branch, remote_url: null, provider: null, web_url: null },
    })
    const folders: Record<string, WorkspaceInspection> = { '/home/ana/app': folder('/home/ana/app', 'main'), '/home/ana/other': folder('/home/ana/other', 'trunk') }
    vi.spyOn(api, 'inspectWorkspace').mockImplementation(async path => folders[path])
    vi.spyOn(api, 'discoverTeam').mockImplementation(async (_id, path) => ({
      discovery_id: 5, project_path: path, project: folders[path],
      detected_stack: { name: 'app', language: 'Python', frameworks: [], databases: [], infra: [], complexity: 'Low', file_count: 2 },
      suggested_team: [],
    }))
    const init = vi.spyOn(api, 'initCompany').mockImplementation(() => new Promise(() => {}))
    renderWizard()
    const scan = async (path: string) => {
      fireEvent.change(screen.getByLabelText('Project folder'), { target: { value: path } })
      fireEvent.click(screen.getByRole('button', { name: 'Scan' }))
      await waitFor(() => expect(screen.getByLabelText('Project folder')).toHaveValue(path))
      await screen.findByText(/Stack detected/)
    }

    await scan('/home/ana/app')
    for (let step = 0; step < 5; step++) fireEvent.click(screen.getByRole('button', { name: 'Next →' }))
    fireEvent.change(screen.getByLabelText('Base branch task branches start from'), { target: { value: 'release' } })
    for (let step = 0; step < 5; step++) fireEvent.click(screen.getByRole('button', { name: /Back/ }))

    await scan('/home/ana/other')
    for (let step = 0; step < 5; step++) fireEvent.click(screen.getByRole('button', { name: 'Next →' }))
    expect(screen.getByLabelText('Base branch task branches start from')).toHaveValue('trunk')
    fireEvent.click(screen.getByRole('button', { name: /Initialize Factory/ }))
    await waitFor(() => expect(init).toHaveBeenCalledOnce())
    expect(init.mock.calls[0][0]).toMatchObject({ project_path: '/home/ana/other', git_workflow: true, git_base_branch: 'trunk' })
  })

  it('scans with one inspection when leaving the field by clicking Scan', async () => {
    let resolveInspect!: (value: WorkspaceInspection) => void
    const inspect = vi.spyOn(api, 'inspectWorkspace')
      .mockImplementation(() => new Promise(resolve => { resolveInspect = resolve }))
    const discover = vi.spyOn(api, 'discoverTeam').mockImplementation(() => new Promise(() => {}))
    renderWizard()

    const input = screen.getByLabelText('Project folder')
    fireEvent.change(input, { target: { value: '~/code/la-fabrique' } })
    fireEvent.blur(input)
    expect(await screen.findByText('Checking folder…')).toBeInTheDocument()
    const scan = screen.getByRole('button', { name: 'Scan' })
    expect(scan).toBeEnabled()
    fireEvent.click(scan)

    await act(async () => { resolveInspect(githubProject) })
    await waitFor(() => expect(discover).toHaveBeenCalledWith(null, '/Users/ana/code/la-fabrique'))
    expect(inspect).toHaveBeenCalledOnce()
    expect(inspect).toHaveBeenCalledWith('~/code/la-fabrique')
  })

  it('shows the Windows start command and placeholder when the desktop app runs on Windows', () => {
    daemon.status = { data: { connected: false, host: null }, isPending: false, error: null }
    window.electronAPI = {
      platform: 'win32',
      versions: { node: '', chrome: '', electron: '' },
      selectDirectory: async () => null,
      onNavigate: () => () => {},
      getBackendUrl: async () => '',
      getAppVersion: async () => '',
      openExternal: async () => {},
    }
    renderWizard()
    expect(screen.getByLabelText('Project folder'))
      .toHaveAttribute('placeholder', 'C:\\Users\\you\\source\\repos\\my-app')
    expect(screen.getByText('.\\scripts\\start-daemon.ps1')).toBeInTheDocument()
    expect(screen.queryByText('./scripts/start-daemon.sh')).not.toBeInTheDocument()
  })
})
