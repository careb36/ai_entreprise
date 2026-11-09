import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import GitWorkflowPanel, { GitSetupChoice, gitChoiceFor } from './GitWorkflowPanel'
import { queryClient } from '../lib/queryClient'
import { ApiError, type WorkspaceInspection } from '../services/api'
import { type GitWorkflow, type GitWorkflowChoice, workControlApi } from '../services/workControl'

const OBSERVED: GitWorkflow = {
  company_id: 4,
  git_workflow: true,
  git_base_branch: null,
  base_status: {
    state: 'observed',
    path: '/home/ana/app',
    is_repo: true,
    base_ref: 'main',
    base_commit: '0123456789abcdef',
    base_remote: 'origin/main',
    base_behind_remote: 424,
    base_ahead_of_remote: 0,
  },
}

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>)
}

function inspection(isRepo: boolean, branch: string | null = 'main'): WorkspaceInspection {
  return {
    host: { hostname: 'laptop', platform: 'linux', home: '/home/ana' } as WorkspaceInspection['host'],
    path: '/home/ana/app',
    name: 'app',
    exists: true,
    is_dir: true,
    git: isRepo ? { is_repo: true, branch, remote_url: null, provider: null, web_url: null } : { is_repo: false, branch: null, remote_url: null, provider: null, web_url: null },
  }
}

describe('the Git workflow on the company settings', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('asks the daemon how the folder stands and warns how far the base is behind', async () => {
    const getGit = vi.spyOn(workControlApi, 'getGit').mockResolvedValue(OBSERVED)
    wrap(<GitWorkflowPanel companyId={4} canManage />)

    expect(await screen.findByText(/424 commit\(s\) behind origin\/main/)).toBeInTheDocument()
    expect(getGit).toHaveBeenCalledWith(4, true)
    expect(screen.getByText(/Repository at \/home\/ana\/app; base main at 0123456789ab/)).toBeInTheDocument()
  })

  it('lets a Manager turn it off and name a base branch', async () => {
    vi.spyOn(workControlApi, 'getGit').mockResolvedValue(OBSERVED)
    const setGit = vi.spyOn(workControlApi, 'setGit').mockResolvedValue({ company_id: 4, git_workflow: true, git_base_branch: 'develop' })
    wrap(<GitWorkflowPanel companyId={4} canManage />)

    fireEvent.change(await screen.findByLabelText('Base branch task branches start from'), { target: { value: ' develop ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(setGit).toHaveBeenCalledWith(4, { git_workflow: true, git_base_branch: 'develop' }))
    expect(await screen.findByText('Git settings saved')).toBeInTheDocument()
  })

  it('shows a Viewer the setting without a way to change it', async () => {
    vi.spyOn(workControlApi, 'getGit').mockResolvedValue(OBSERVED)
    wrap(<GitWorkflowPanel companyId={4} canManage={false} />)

    expect(await screen.findByRole('checkbox', { name: /Git branch per task/ })).toBeDisabled()
    expect(screen.getByLabelText('Base branch task branches start from')).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
  })

  it('says a folder is not a repository, and when the check could not run', async () => {
    vi.spyOn(workControlApi, 'getGit').mockResolvedValueOnce({
      company_id: 4, git_workflow: false, git_base_branch: null,
      base_status: { state: 'observed', path: '/home/ana/notes', is_repo: false },
    })
    const first = wrap(<GitWorkflowPanel companyId={4} canManage />)
    expect(await screen.findByText(/\/home\/ana\/notes is not a git repository/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /Git branch per task/ })).toBeDisabled()
    first.unmount()

    queryClient.clear()
    vi.spyOn(workControlApi, 'getGit').mockResolvedValueOnce({
      company_id: 4, git_workflow: true, git_base_branch: null,
      base_status: { state: 'unknown', reason: 'No workspace daemon answered' },
    })
    wrap(<GitWorkflowPanel companyId={4} canManage />)
    expect(await screen.findByText('Could not check the folder just now: No workspace daemon answered')).toBeInTheDocument()
  })

  it('never offers one company\'s settings to save on another whose read failed', async () => {
    vi.spyOn(workControlApi, 'getGit').mockImplementation(async id => {
      if (id === 4) return { ...OBSERVED, git_base_branch: 'release' }
      throw new ApiError(403, 'Forbidden', 'Not a member of this company')
    })
    const setGit = vi.spyOn(workControlApi, 'setGit')
    const view = wrap(<GitWorkflowPanel companyId={4} canManage />)
    expect(await screen.findByLabelText('Base branch task branches start from')).toHaveValue('release')

    // The same panel instance, now for company 5 (as if not re-keyed).
    view.rerender(<QueryClientProvider client={queryClient}><GitWorkflowPanel companyId={5} canManage /></QueryClientProvider>)
    expect(await screen.findByText('Not a member of this company')).toBeInTheDocument()
    expect(screen.queryByLabelText('Base branch task branches start from')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    expect(setGit).not.toHaveBeenCalled()
  })

  it('drops the observation of the old base after saving another, and asks the daemon again', async () => {
    const getGit = vi.spyOn(workControlApi, 'getGit')
      .mockResolvedValueOnce(OBSERVED)
      .mockImplementation(() => new Promise(() => {}))
    vi.spyOn(workControlApi, 'setGit').mockResolvedValue({ company_id: 4, git_workflow: true, git_base_branch: 'develop' })
    wrap(<GitWorkflowPanel companyId={4} canManage />)
    expect(await screen.findByText(/424 commit\(s\) behind origin\/main/)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Base branch task branches start from'), { target: { value: 'develop' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText('Git settings saved')).toBeInTheDocument()
    expect(screen.queryByText(/424 commit\(s\) behind/)).not.toBeInTheDocument()
    expect(queryClient.getQueryData<GitWorkflow>(['company', 4, 'git', 'checked'])?.base_status).toBeUndefined()
    await waitFor(() => expect(getGit).toHaveBeenCalledTimes(2))
    expect(getGit).toHaveBeenLastCalledWith(4, true)
  })

  it('names a diverged base and an unreachable remote', async () => {
    vi.spyOn(workControlApi, 'getGit').mockResolvedValue({
      ...OBSERVED,
      base_status: { ...OBSERVED.base_status!, base_behind_remote: 3, base_ahead_of_remote: 2 },
    })
    wrap(<GitWorkflowPanel companyId={4} canManage />)
    expect(await screen.findByText(/have diverged \(2 commit\(s\) only here, 3 only there\)/)).toBeInTheDocument()
  })
})

function Wizard({ inspected, onChoice }: { inspected: WorkspaceInspection | null; onChoice: (c: GitWorkflowChoice) => void }) {
  const [choice, setChoice] = useState<GitWorkflowChoice | null>(null)
  return (
    <GitSetupChoice inspection={inspected} choice={choice}
      onChange={value => { setChoice(value); onChoice(gitChoiceFor(inspected, value)) }} />
  )
}

describe('the Git choice in the New company wizard', () => {
  it('is pre-set from the inspection with the checked-out branch as base', () => {
    expect(gitChoiceFor(inspection(true, 'main'), null)).toEqual({ git_workflow: true, git_base_branch: 'main' })
    expect(gitChoiceFor(inspection(false), null)).toEqual({ git_workflow: false, git_base_branch: null })
    expect(gitChoiceFor(null, null)).toEqual({ git_workflow: false, git_base_branch: null })
    // A folder that is not a repository keeps it off whatever was ticked.
    expect(gitChoiceFor(inspection(false), { git_workflow: true, git_base_branch: 'main' }).git_workflow).toBe(false)

    const onChoice = vi.fn()
    wrap(<Wizard inspected={inspection(true, 'main')} onChoice={onChoice} />)
    const box = screen.getByRole('checkbox', { name: /Git branch per task/ })
    expect(box).toBeChecked()
    expect(screen.getByLabelText('Base branch task branches start from')).toHaveValue('main')
    fireEvent.click(box)
    expect(onChoice).toHaveBeenLastCalledWith({ git_workflow: false, git_base_branch: 'main' })
  })

  it('cannot turn it on for a folder that is not a repository', () => {
    wrap(<Wizard inspected={inspection(false)} onChoice={vi.fn()} />)
    expect(screen.getByText(/This folder is not a git repository/)).toBeInTheDocument()
    const box = screen.getByRole('checkbox', { name: /Git branch per task/ })
    expect(box).not.toBeChecked()
    expect(box).toBeDisabled()
  })
})
