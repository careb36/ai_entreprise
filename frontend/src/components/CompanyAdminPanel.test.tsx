import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CompanyAdminPanel from './CompanyAdminPanel'
import { queryClient } from '../lib/queryClient'
import { api } from '../services/api'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function wrap(isOwner = true) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><CompanyAdminPanel companyId={4} isOwner={isOwner} /></MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('renaming and deleting the company', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4, companyName: 'Acme' })
    useAuthStore.setState({ loadMe: vi.fn().mockResolvedValue(undefined) })
  })
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('lets an Owner rename it', async () => {
    const rename = vi.spyOn(api, 'renameCompany').mockResolvedValue({ id: 4, company_name: 'La Fabrique' })
    wrap()
    fireEvent.change(screen.getByLabelText('Company name'), { target: { value: 'La Fabrique' } })
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }))
    await waitFor(() => expect(rename).toHaveBeenCalledWith(4, 'La Fabrique'))
    await waitFor(() => expect(useAppStore.getState().companyName).toBe('La Fabrique'))
  })

  it('deletes only once the name is typed back, and lists what goes', async () => {
    vi.spyOn(api, 'companyDeletion').mockResolvedValue({ rows: { sprints: 2, agents: 6 }, blockers: [] })
    const remove = vi.spyOn(api, 'deleteCompany').mockResolvedValue({ company_id: 4, company_name: 'Acme', rows: {} })
    wrap()
    fireEvent.click(screen.getByRole('button', { name: /Delete company/ }))
    expect(await screen.findByText('agents')).toBeInTheDocument()
    const confirm = screen.getByRole('button', { name: 'Delete permanently' })
    expect(confirm).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/Type Acme to confirm/), { target: { value: 'Acme' } })
    fireEvent.click(confirm)
    await waitFor(() => expect(remove).toHaveBeenCalledWith(4, 'Acme'))
    await waitFor(() => expect(useAppStore.getState().companyId).toBeNull())
  })

  it('is held back while a sprint runs, and closed to anyone but an Owner', async () => {
    vi.spyOn(api, 'companyDeletion').mockResolvedValue({ rows: {}, blockers: ['sprint_running'] })
    const { unmount } = wrap()
    fireEvent.click(screen.getByRole('button', { name: /Delete company/ }))
    expect(await screen.findByText(/A sprint is running/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/Type Acme to confirm/), { target: { value: 'Acme' } })
    expect(screen.getByRole('button', { name: 'Delete permanently' })).toBeDisabled()
    unmount()
    wrap(false)
    expect(screen.getByRole('button', { name: /Delete company/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Rename' })).toBeDisabled()
  })
})
