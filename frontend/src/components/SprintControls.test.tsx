import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SprintControls, SprintStatusBanner, StartChoice } from './SprintControls'
import { queryClient } from '../lib/queryClient'
import { workControlApi, type SprintState } from '../services/workControl'
import { api } from '../services/api'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

const sprint: SprintState = {
  id: 8, company_id: 1, sprint_name: 'S0', status: 'active', start_date: '', scope_size: 'M', item_limit: 8,
}

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>)
}

function asRole(role: 'Manager' | 'Viewer') {
  useAuthStore.setState({ memberships: [{ company_id: 1, human_role: role }] } as never)
}

describe('sprint work controls', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 1 })
    asRole('Manager')
    vi.spyOn(workControlApi, 'getAutonomy').mockResolvedValue({ company_id: 1, autonomy_level: 2 })
    vi.spyOn(workControlApi, 'getGit').mockResolvedValue({ company_id: 1, git_workflow: false, git_base_branch: null })
  })
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('offers Start for a planned sprint and starts it', async () => {
    const start = vi.spyOn(workControlApi, 'startSprint').mockResolvedValue({ status: 'active', dispatched: 3 })
    wrap(<SprintStatusBanner sprint={{ ...sprint, status: 'planned' }} />)
    expect(screen.getByText('This sprint has not started')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Start' }))
    await waitFor(() => expect(start).toHaveBeenCalledWith(8))
  })

  it('says why the worker paused a sprint and resumes it', async () => {
    const resume = vi.spyOn(api, 'resumeSprint').mockResolvedValue({ status: 'active' })
    wrap(<SprintStatusBanner sprint={{ ...sprint, status: 'paused', pause_kind: 'no_credit', pause_reason: 'No credit at deepseek: top up.' }} />)
    expect(screen.getByText('Paused: a model provider has no credit')).toBeInTheDocument()
    expect(screen.getByText("Top up the provider's account, then resume the sprint.")).toBeInTheDocument()
    expect(screen.getByText('Recorded reason: No credit at deepseek: top up.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    await waitFor(() => expect(resume).toHaveBeenCalledWith(8))
  })

  it('says why a sprint with nothing done paused and what to do', () => {
    const reason = 'No task of this sprint is done and 2 wait for a person: unblock, finish or discard them, then resume the sprint.'
    wrap(<SprintStatusBanner sprint={{ ...sprint, status: 'paused', pause_kind: 'nothing_done', pause_reason: reason }} />)
    expect(screen.getByText('Paused: no task is done and the rest wait for a person')).toBeInTheDocument()
    expect(screen.getByText('Unblock, finish or discard the blocked tasks, then resume the sprint.')).toBeInTheDocument()
    expect(screen.getByText(`Recorded reason: ${reason}`)).toBeInTheDocument()
  })

  it('says a sprint paused because its test command found no tests', () => {
    const reason = 'No delivery of this sprint could run its tests: the test command found no tests at tests (file or directory not found).'
    wrap(<SprintStatusBanner sprint={{ ...sprint, status: 'paused', pause_kind: 'tests_not_found', pause_reason: reason }} />)
    expect(screen.getByText('Paused: the test command found no tests')).toBeInTheDocument()
    expect(screen.getByText(/Fix the company's test command/)).toBeInTheDocument()
    expect(screen.getByText(`Recorded reason: ${reason}`)).toBeInTheDocument()
  })

  it('says a person paused a sprint with no recorded reason', () => {
    wrap(<SprintStatusBanner sprint={{ ...sprint, status: 'paused', pause_kind: 'person', pause_reason: null }} />)
    expect(screen.getByText('Paused by a person')).toBeInTheDocument()
    expect(screen.queryByText(/Recorded reason/)).not.toBeInTheDocument()
  })

  it('shows nothing while the sprint runs', () => {
    const { container } = wrap(<SprintStatusBanner sprint={sprint} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('stops a sprint only after the person confirms', async () => {
    const stop = vi.spyOn(workControlApi, 'stopSprint').mockResolvedValue({ status: 'cancelled', stopped_tasks: 2 })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    wrap(<SprintControls sprint={sprint} />)
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(stop).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    await waitFor(() => expect(stop).toHaveBeenCalledWith(8))
    expect(confirm).toHaveBeenCalledTimes(2)
  })

  it('offers neither Pause nor Stop on a finished sprint', () => {
    wrap(<SprintControls sprint={{ ...sprint, status: 'cancelled' }} />)
    expect(screen.queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Dispatch pending' })).toBeDisabled()
  })

  it('lets a Manager change the autonomy level and a Viewer only read it', async () => {
    const set = vi.spyOn(workControlApi, 'setAutonomy').mockResolvedValue({ company_id: 1, autonomy_level: 1 })
    const { unmount } = wrap(<SprintControls sprint={sprint} />)
    const select = await screen.findByRole('combobox')
    expect(select).toHaveValue('2')
    fireEvent.change(select, { target: { value: '1' } })
    await waitFor(() => expect(set).toHaveBeenCalledWith(1, 1))
    unmount()
    queryClient.clear()
    asRole('Viewer')
    wrap(<SprintControls sprint={sprint} />)
    expect(await screen.findByRole('combobox')).toBeDisabled()
  })

  it('lets a Manager put each task on its own Git branch', async () => {
    const set = vi.spyOn(workControlApi, 'setGit').mockResolvedValue({ company_id: 1, git_workflow: true, git_base_branch: null })
    wrap(<SprintControls sprint={sprint} />)
    const toggle = await screen.findByRole('checkbox', { name: 'Git branch per task' })
    expect(toggle).not.toBeChecked()
    fireEvent.click(toggle)
    await waitFor(() => expect(set).toHaveBeenCalledWith(1, { git_workflow: true, git_base_branch: null }))
  })

  it('in the wizard, level 1 never starts on its own', () => {
    const onChange = vi.fn()
    const { rerender } = wrap(<StartChoice checked humanLed={false} onChange={onChange} />)
    expect(screen.getByRole('checkbox')).toBeChecked()
    fireEvent.click(screen.getByRole('checkbox'))
    expect(onChange).toHaveBeenCalledWith(false)
    rerender(<QueryClientProvider client={queryClient}><StartChoice checked humanLed onChange={onChange} /></QueryClientProvider>)
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    expect(screen.getByRole('checkbox')).toBeDisabled()
    expect(screen.getByText('At level 1 you always start it yourself.')).toBeInTheDocument()
  })
})
