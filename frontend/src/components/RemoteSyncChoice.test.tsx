import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RemoteSyncChoice, remoteChoiceFor, type RemoteChoice } from './RemoteSyncChoice'
import { BaseBehindNotice } from './BaseSyncAction'
import { queryClient } from '../lib/queryClient'
import { api, type ForgePolicy, type WorkspaceInspection } from '../services/api'
import { baseSyncApi, type FolderGitCheck } from '../services/baseSync'
import { workControlApi } from '../services/workControl'

const REMOTE = 'https://example.invalid/team/app.git'

function inspection(remote: string | null, isRepo = true): WorkspaceInspection {
  return {
    host: { hostname: 'laptop', platform: 'linux', home: '/home/dev' } as WorkspaceInspection['host'],
    path: '/home/dev/app',
    name: 'app',
    exists: true,
    is_dir: true,
    git: { is_repo: isRepo, branch: isRepo ? 'main' : null, remote_url: remote, provider: null, web_url: null },
  }
}

const LOCAL: FolderGitCheck = {
  state: 'observed',
  is_repo: true,
  base_ref: 'main',
  current_branch: 'main',
  clean: true,
  operation_in_progress: null,
  remote_url: REMOTE,
  remote_contacted: false,
  forge_policy_max: 'publish',
}

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>)
}

function Harness({ folder }: { folder: WorkspaceInspection }) {
  const [choice, setChoice] = useState<RemoteChoice | null>(null)
  return (
    <>
      <RemoteSyncChoice inspection={folder} base="main" choice={choice} onChange={setChoice} />
      <output data-testid="payload">{JSON.stringify(remoteChoiceFor(folder, choice, 'publish'))}</output>
    </>
  )
}

function capabilities(ceiling: ForgePolicy) {
  vi.spyOn(api, 'capabilities').mockResolvedValue({
    agent_graph: false, memory: false, github: false, auth_required: true, forge_policy_max: ceiling,
  })
}

describe('the New company form: the remote and the local copy (#745)', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('defaults to fetch only when the folder has a remote, capped by the ceiling', () => {
    expect(remoteChoiceFor(inspection(REMOTE), null, 'publish')).toEqual({ forge_policy: 'fetch', sync_base: false })
    expect(remoteChoiceFor(inspection(null), null, 'publish')).toEqual({ forge_policy: 'none', sync_base: false })
    expect(remoteChoiceFor(inspection(REMOTE), null, 'none')).toEqual({ forge_policy: 'none', sync_base: false })
    expect(remoteChoiceFor(inspection(REMOTE, false), null, 'publish')).toEqual({})
    expect(remoteChoiceFor(null, null, 'publish')).toEqual({})
    // Under none the box is never sent ticked.
    expect(remoteChoiceFor(inspection(REMOTE), { forge_policy: 'none', sync_base: true }, 'publish'))
      .toEqual({ forge_policy: 'none', sync_base: false })
    expect(remoteChoiceFor(inspection(REMOTE), { forge_policy: 'publish', sync_base: true }, 'fetch'))
      .toEqual({ forge_policy: 'fetch', sync_base: true })
  })

  it('shows what was read locally and contacts the remote only on "Check remote"', async () => {
    capabilities('publish')
    const check = vi.spyOn(baseSyncApi, 'checkFolder').mockImplementation(async (_path, _base, contact) => contact
      ? { ...LOCAL, remote_contacted: true, base_remote: 'origin/main', base_commit: 'abc', base_behind_remote: 5, base_ahead_of_remote: 0 }
      : LOCAL)
    wrap(<Harness folder={inspection(REMOTE)} />)

    expect(await screen.findByText('Clean')).toBeInTheDocument()
    expect(screen.getByText(REMOTE)).toBeInTheDocument()
    expect(check).toHaveBeenCalledTimes(1)
    expect(check).toHaveBeenLastCalledWith('/home/dev/app', 'main', false)
    expect(screen.getByText(/This contacts https:\/\/example.invalid/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Check remote' }))
    expect(await screen.findByText(/5 commit\(s\) behind origin\/main/)).toBeInTheDocument()
    expect(check).toHaveBeenLastCalledWith('/home/dev/app', 'main', true)
  })

  it('selects fetch by default, offers the box, and turns it off with none', async () => {
    capabilities('publish')
    vi.spyOn(baseSyncApi, 'checkFolder').mockResolvedValue(LOCAL)
    wrap(<Harness folder={inspection(REMOTE)} />)

    const fetch = screen.getByRole('radio', { name: /Read from the remote \(fetch\)/ })
    expect(fetch).toBeChecked()
    const box = screen.getByRole('checkbox', { name: /Bring my local copy up to date/ })
    expect(box).toBeEnabled()
    fireEvent.click(box)
    expect(JSON.parse(screen.getByTestId('payload').textContent ?? '')).toEqual({ forge_policy: 'fetch', sync_base: true })

    fireEvent.click(screen.getByRole('radio', { name: /Local only \(none\)/ }))
    expect(screen.getByRole('checkbox', { name: /Bring my local copy up to date/ })).toBeDisabled()
    expect(JSON.parse(screen.getByTestId('payload').textContent ?? '')).toEqual({ forge_policy: 'none', sync_base: false })
  })

  it('offers nothing above the deployment ceiling', async () => {
    capabilities('none')
    vi.spyOn(baseSyncApi, 'checkFolder').mockResolvedValue({ ...LOCAL, forge_policy_max: 'none' })
    wrap(<RemoteSyncChoice inspection={inspection(REMOTE)} base="main" choice={null} onChange={() => {}} />)

    await waitFor(() => expect(screen.getByRole('radio', { name: /Read from the remote/ })).toBeDisabled())
    expect(screen.getByRole('radio', { name: /Local only/ })).toBeChecked()
  })

  it('is not shown for a folder that is not a repository', () => {
    capabilities('publish')
    const check = vi.spyOn(baseSyncApi, 'checkFolder')
    const { container } = wrap(<RemoteSyncChoice inspection={inspection(null, false)} base={null} choice={null} onChange={() => {}} />)
    expect(container).toBeEmptyDOMElement()
    expect(check).not.toHaveBeenCalled()
  })
})

describe('the board before a sprint starts (#745)', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('says how far the local copy is behind and lets a Manager bring it up to date', async () => {
    vi.spyOn(workControlApi, 'getGit').mockResolvedValue({
      company_id: 4, git_workflow: true, git_base_branch: null,
      base_status: { state: 'observed', is_repo: true, base_ref: 'main', base_remote: 'origin/main', base_behind_remote: 3, base_ahead_of_remote: 0 },
    })
    const sync = vi.spyOn(baseSyncApi, 'syncCompany').mockResolvedValue({
      ok: true, outcome: 'updated', base: 'main', remote: 'origin/main', behind: 3, ahead: 0, before: 'a', after: 'b',
    })
    wrap(<BaseBehindNotice companyId={4} canManage />)

    expect(await screen.findByText(/Your local copy is 3 commit\(s\) behind origin\/main/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Bring the local copy up to date' }))
    expect(await screen.findByText('main brought up to date: 3 commit(s) from origin/main.')).toBeInTheDocument()
    expect(sync).toHaveBeenCalledWith(4)
  })

  it('tells a Viewer too, without the action', async () => {
    vi.spyOn(workControlApi, 'getGit').mockResolvedValue({
      company_id: 4, git_workflow: true, git_base_branch: null,
      base_status: { state: 'observed', is_repo: true, base_ref: 'main', base_remote: 'origin/main', base_behind_remote: 2, base_ahead_of_remote: 0 },
    })
    wrap(<BaseBehindNotice companyId={4} canManage={false} />)
    expect(await screen.findByText(/2 commit\(s\) behind/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
