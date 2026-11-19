import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Sprints from './Sprints'
import { api, ApiError, type SprintSummary } from '../services/api'
import { specificationsApi } from '../services/specifications'
import { managerApi, type ManagerRecord } from '../services/manager'
import { sprintAdminApi, type ManagedSprint } from '../services/sprintAdmin'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

const base: Omit<SprintSummary, 'id' | 'sprint_name' | 'status'> = {
  goal: null, start_date: null, scope_size: 'S', total: 2, done: 0, blocked: 0, rework: 0,
  second_attempts: { total: 0, done: 0, briefed: 0, briefed_done: 0 },
  console_advice: { advised: { total: 0, done: 0, rework: 0 }, unadvised: { total: 0, done: 0, rework: 0 } },
  architect_advice: { advised: { total: 0, done: 0, rework: 0 }, unadvised: { total: 0, done: 0, rework: 0 } },
}
const running: ManagedSprint = { ...base, id: 9, sprint_name: 'Hardening', status: 'active', goal: 'Close gaps' }
const stopped: ManagedSprint = { ...base, id: 4, sprint_name: 'Foundation', status: 'cancelled' }
const archived: ManagedSprint = {
  ...base, id: 2, sprint_name: 'Old work', status: 'completed', archived_at: '2026-11-01T10:00:00', archived_by: 'ana@example.com',
}

const counts = (): ManagerRecord['total'] => ({
  suggested: 0, decided: 0, agreed: 0, unusable: 0, taken_alone: 0, overridden: 0, override_rate: null,
})

function asRole(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 42, human_role: role }] })
}

function renderPage() {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/sprints']}><Sprints /></MemoryRouter>
    </QueryClientProvider>,
  )
}

async function row(name: string) {
  const list = await screen.findByRole('list', { name: 'Sprints' })
  const item = within(list).getAllByRole('listitem').find(li => within(li).queryByRole('link', { name }))
  if (!item) throw new Error(`no row for ${name}`)
  return item
}

describe('Sprint edit, delete and archive', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 42, companyName: 'Example', activeSprintId: null })
    vi.spyOn(specificationsApi, 'list').mockResolvedValue({ specifications: [] })
    vi.spyOn(api, 'getBenchScorecards').mockResolvedValue({ available: true, cards: [] })
    vi.spyOn(managerApi, 'record').mockResolvedValue({ categories: {}, total: counts(), flagged_rules: [] })
    vi.spyOn(api, 'listSprints').mockResolvedValue([running, stopped])
  })
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
    useAuthStore.setState({ memberships: [] })
  })

  it('shows a Viewer no actions', async () => {
    asRole('Viewer')
    renderPage()
    const foundation = await row('Foundation')
    expect(within(foundation).queryByRole('button', { name: /Edit/ })).toBeNull()
    expect(within(foundation).queryByRole('button', { name: /Delete/ })).toBeNull()
  })

  it('lets a Manager edit but not delete, and sends only the editable fields', async () => {
    asRole('Manager')
    const update = vi.spyOn(sprintAdminApi, 'update').mockResolvedValue({ ...running, changed: {} })
    renderPage()
    const hardening = await row('Hardening')
    expect(within(hardening).queryByRole('button', { name: /Delete/ })).toBeNull()
    fireEvent.click(within(hardening).getByRole('button', { name: /Edit/ }))
    const form = within(hardening).getByRole('form', { name: 'Edit sprint' })
    fireEvent.change(within(form).getByLabelText('Name'), { target: { value: '  Hardening II ' } })
    fireEvent.change(within(form).getByLabelText('Goal'), { target: { value: '' } })
    fireEvent.change(within(form).getByLabelText('Days'), { target: { value: '120' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    expect(within(form).getByRole('alert')).toHaveTextContent('from 1 to 90')
    expect(update).not.toHaveBeenCalled()
    fireEvent.change(within(form).getByLabelText('Days'), { target: { value: '10' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(update).toHaveBeenCalledWith(9, { sprint_name: 'Hardening II', goal: null, duration_days: 10 }))
  })

  it('lets an Owner delete a halted sprint only after confirming, and not a running one', async () => {
    asRole('Owner')
    const remove = vi.spyOn(sprintAdminApi, 'remove').mockResolvedValue({
      archived: true, sprint_id: 4, archived_at: '2026-11-08T10:00:00', archived_by: 'me', evidence: ['task_events'],
    })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    renderPage()
    const hardening = await row('Hardening')
    const runningDelete = within(hardening).getByRole('button', { name: /Delete/ })
    expect(runningDelete).toBeDisabled()
    expect(runningDelete).toHaveAttribute('title', 'Stop the sprint first')

    const foundation = await row('Foundation')
    fireEvent.click(within(foundation).getByRole('button', { name: /Delete/ }))
    expect(confirm.mock.calls[0][0]).toContain('archived, not deleted: its evidence stays')
    expect(remove).not.toHaveBeenCalled()
    fireEvent.click(within(foundation).getByRole('button', { name: /Delete/ }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith(4))
  })

  it('shows the backend refusal', async () => {
    asRole('Owner')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(sprintAdminApi, 'remove').mockRejectedValue(new ApiError(409, 'Conflict', 'Stop the sprint first; this one is active'))
    renderPage()
    fireEvent.click(within(await row('Foundation')).getByRole('button', { name: /Delete/ }))
    expect(await screen.findByText('Stop the sprint first; this one is active')).toBeInTheDocument()
  })

  it('lists archived sprints on request, and an Owner unarchives them', async () => {
    asRole('Owner')
    const all = vi.spyOn(sprintAdminApi, 'listWithArchived').mockResolvedValue([running, stopped, archived])
    const unarchive = vi.spyOn(sprintAdminApi, 'unarchive').mockResolvedValue({ archived: false, sprint_id: 2, status: 'completed' })
    renderPage()
    await row('Foundation')
    expect(screen.queryByRole('link', { name: 'Old work' })).toBeNull()
    expect(all).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Show archived sprints' }))
    const old = await row('Old work')
    expect(all).toHaveBeenCalledWith(42)
    expect(old).toHaveTextContent('Archived by ana@example.com')
    expect(within(old).queryByRole('button', { name: /Edit/ })).toBeNull()
    expect(within(old).queryByRole('button', { name: /Delete/ })).toBeNull()
    fireEvent.click(within(old).getByRole('button', { name: /Unarchive/ }))
    await waitFor(() => expect(unarchive).toHaveBeenCalledWith(2))
  })

  it('shows a Manager archived sprints without Unarchive', async () => {
    asRole('Manager')
    vi.spyOn(sprintAdminApi, 'listWithArchived').mockResolvedValue([archived])
    renderPage()
    await screen.findByRole('list', { name: 'Sprints' })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Show archived sprints' }))
    const old = await row('Old work')
    expect(within(old).queryByRole('button', { name: /Unarchive/ })).toBeNull()
  })

  it('asks the backend for archived sprints with include_archived', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue([])
    await sprintAdminApi.listWithArchived(42)
    expect(get).toHaveBeenCalledWith('/v1/sprints', { company_id: 42, include_archived: 'true' })
  })
})
