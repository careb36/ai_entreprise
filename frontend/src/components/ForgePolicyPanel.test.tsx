import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ForgePolicyPanel from './ForgePolicyPanel'
import { queryClient } from '../lib/queryClient'
import { api, type ForgePolicySetting } from '../services/api'

const LOCAL: ForgePolicySetting = {
  company_id: 4,
  forge_policy: 'none',
  effective: 'none',
  ceiling: 'publish',
  policies: ['none', 'fetch', 'publish'],
}

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>)
}

describe('the forge policy on the company settings', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('shows each value with its explanation and lets an Owner change it', async () => {
    vi.spyOn(api, 'getForgePolicy').mockResolvedValue(LOCAL)
    const set = vi.spyOn(api, 'setForgePolicy').mockResolvedValue({ ...LOCAL, forge_policy: 'fetch', effective: 'fetch' })
    wrap(<ForgePolicyPanel companyId={4} isOwner />)

    const local = await screen.findByRole('radio', { name: /Local only/ })
    expect(local).toBeChecked()
    expect(screen.getByText(/No remote is ever contacted/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: /Read the base from its remote/ }))

    await waitFor(() => expect(set).toHaveBeenCalledWith(4, 'fetch'))
    expect(await screen.findByRole('radio', { name: /Read the base from its remote/ })).toBeChecked()
  })

  it('is read-only for anyone but an Owner', async () => {
    vi.spyOn(api, 'getForgePolicy').mockResolvedValue({ ...LOCAL, forge_policy: 'publish', effective: 'publish' })
    const set = vi.spyOn(api, 'setForgePolicy')
    wrap(<ForgePolicyPanel companyId={4} isOwner={false} />)

    const publish = await screen.findByRole('radio', { name: /Read and publish/ })
    expect(publish).toBeChecked()
    expect(publish).toBeDisabled()
    expect(screen.getByText('Only an Owner changes the forge policy.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: /Local only/ }))
    expect(set).not.toHaveBeenCalled()
  })

  it('says when the deployment caps the stored value and offers nothing above the cap', async () => {
    vi.spyOn(api, 'getForgePolicy').mockResolvedValue({ ...LOCAL, forge_policy: 'publish', effective: 'fetch', ceiling: 'fetch' })
    wrap(<ForgePolicyPanel companyId={4} isOwner />)

    expect(await screen.findByRole('note')).toHaveTextContent(/at most “fetch”/)
    expect(screen.getByRole('radio', { name: /Read and publish/ })).toBeDisabled()
    expect(screen.getByRole('radio', { name: /Local only/ })).toBeEnabled()
  })
})
