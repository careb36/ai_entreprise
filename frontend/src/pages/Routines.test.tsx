import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Routines from './Routines'
import { queryClient } from '../lib/queryClient'
import { api, type Routine } from '../services/api'
import { useAppStore } from '../store/useAppStore'

const roleState = vi.hoisted(() => ({ role: 'Manager' as string | null }))

vi.mock('../hooks/useQueries', () => ({
  useAgents: () => ({ data: { agents: [{ role: 'Developer' }, { role: 'QA' }] } }),
  useCurrentRole: () => roleState.role,
}))

const ROUTINE: Routine = {
  id: 5,
  company_id: 1,
  title: 'Dependency audit',
  description: 'Audit the {{scope}} dependencies.',
  assignee: 'Developer',
  schedule: { every: 'week', at: '09:00', timezone: 'Europe/Madrid', weekday: 0 },
  enabled: true,
  variables: [{ name: 'scope', default: 'backend', required: false, options: ['backend', 'frontend'] }],
  revision: 2,
  next_run_at: '2026-11-23T08:00:00',
  last_run_at: null,
  last_sprint_id: null,
}

function wrap() {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><Routines /></MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('the Routines page', () => {
  beforeEach(() => {
    queryClient.clear()
    roleState.role = 'Manager'
    useAppStore.setState({ companyId: 1, companyName: 'Acme' })
    vi.spyOn(api, 'getRoutines').mockResolvedValue({ company_id: 1, routines: [ROUTINE] })
  })
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
    useAppStore.setState({ companyId: null, companyName: null })
  })

  it('lists a routine with its schedule and revision', async () => {
    wrap()
    expect(await screen.findByText('Dependency audit')).toBeInTheDocument()
    expect(screen.getByText(/every Monday at 09:00 \(Europe\/Madrid\)/)).toBeInTheDocument()
    expect(screen.getByText(/revision 2/)).toBeInTheDocument()
  })

  it('runs a routine with values for its variables', async () => {
    const run = vi.spyOn(api, 'runRoutine').mockResolvedValue({ sprint_id: 9, revision: 2, values: { scope: 'frontend' } })
    wrap()
    fireEvent.click(await screen.findByRole('button', { name: /run now/i }))
    fireEvent.change(screen.getByRole('combobox', { name: /scope/ }), { target: { value: 'frontend' } })
    fireEvent.click(screen.getByRole('button', { name: /run with these values/i }))

    await waitFor(() => expect(run).toHaveBeenCalledWith(5, { scope: 'frontend' }))
    expect(await screen.findByText('Sprint #9 opened with revision 2.')).toBeInTheDocument()
  })

  it('creates a routine with a variable', async () => {
    const create = vi.spyOn(api, 'createRoutine').mockResolvedValue(ROUTINE)
    wrap()
    fireEvent.click(await screen.findByRole('button', { name: 'New routine' }))
    const form = screen.getByRole('button', { name: 'Create routine' }).closest('form') as HTMLFormElement
    fireEvent.change(within(form).getByRole('textbox', { name: 'Title' }), { target: { value: 'Weekly report' } })
    fireEvent.change(within(form).getByRole('textbox', { name: /what to do/i }), { target: { value: 'Report on {{area}}' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Add variable' }))
    fireEvent.change(within(form).getByRole('textbox', { name: 'Name' }), { target: { value: 'area' } })
    fireEvent.change(within(form).getByRole('textbox', { name: 'Default' }), { target: { value: 'api' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Create routine' }))

    await waitFor(() => expect(create).toHaveBeenCalled())
    const [companyId, data] = create.mock.calls[0]
    expect(companyId).toBe(1)
    expect(data).toMatchObject({
      title: 'Weekly report',
      assignee: 'Developer',
      schedule: { every: 'day', at: '09:00' },
      variables: [{ name: 'area', default: 'api', required: false, options: [] }],
    })
    expect(data.schedule).not.toHaveProperty('weekday')
  })

  it('restores an earlier revision after a second click', async () => {
    vi.spyOn(api, 'getRoutineRevisions').mockResolvedValue({
      routine_id: 5,
      revision: 2,
      revisions: [
        { number: 2, snapshot: { ...ROUTINE }, change_summary: 'Changed description', restored_from: null, created_by_user_id: 1, created_at: null },
        { number: 1, snapshot: { ...ROUTINE }, change_summary: 'Created', restored_from: null, created_by_user_id: 1, created_at: null },
      ],
    })
    const restore = vi.spyOn(api, 'restoreRoutineRevision').mockResolvedValue({ ...ROUTINE, revision: 3 })
    wrap()
    fireEvent.click(await screen.findByRole('button', { name: /revisions/i }))
    fireEvent.click(await screen.findByRole('button', { name: 'Restore' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm restore' }))
    await waitFor(() => expect(restore).toHaveBeenCalledWith(5, 1))
  })

  it('shows a Viewer the routines without the actions', async () => {
    roleState.role = 'Viewer'
    wrap()
    expect(await screen.findByText('Dependency audit')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /run now/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'New routine' })).not.toBeInTheDocument()
  })
})
