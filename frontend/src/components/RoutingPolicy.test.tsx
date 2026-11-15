import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import RoutingPolicy from './RoutingPolicy'
import { modelsApi, type ModelSettings } from '../services/models'
import { queryClient } from '../lib/queryClient'

const SETTINGS: ModelSettings = {
  default_model: 'conn:1:m',
  role_models: { QA: 'conn:1:q' },
  roles: ['Developer', 'QA'],
  effective: {},
  model_policy: null,
  deployment_policy: { bands: { low: 'deployed-cheap' }, label: 'deployed' },
  policy_source: 'deployment',
  difficulty_bands: ['low', 'medium', 'high'],
}

function renderPolicy(canManage = true) {
  return render(
    <QueryClientProvider client={queryClient}>
      <RoutingPolicy companyId={7} canManage={canManage} />
    </QueryClientProvider>,
  )
}

describe('RoutingPolicy', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('shows the deployment policy until the company writes its own, and saves it with the stored choices', async () => {
    vi.spyOn(modelsApi, 'settings').mockResolvedValue(SETTINGS)
    const save = vi.spyOn(modelsApi, 'savePolicy').mockImplementation(async (_id, _current, policy) => ({
      ...SETTINGS, model_policy: policy, policy_source: 'company',
    }))
    renderPolicy()

    const low = await screen.findByLabelText('Low difficulty')
    expect(low).toHaveAttribute('placeholder', 'deployed-cheap')
    expect(screen.getByLabelText('High difficulty')).toHaveAttribute('placeholder', 'Role default')
    expect(screen.getByText(/the deployment's applies/)).toBeInTheDocument()

    fireEvent.change(low, { target: { value: ' cheap-model ' } })
    fireEvent.change(screen.getByLabelText('High difficulty'), { target: { value: 'powerful' } })
    fireEvent.change(screen.getByLabelText('Policy name (shown in the records)'), { target: { value: 'cheap-easy' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save the policy' }))

    await waitFor(() => expect(save).toHaveBeenCalled())
    const [companyId, current, policy] = save.mock.calls[0]
    expect(companyId).toBe(7)
    // The PUT replaces the model choices: they are sent back unchanged.
    expect(current).toMatchObject({ default_model: 'conn:1:m', role_models: { QA: 'conn:1:q' } })
    expect(policy).toEqual({ bands: { low: 'cheap-model', high: 'powerful' }, label: 'cheap-easy' })
    expect(await screen.findByText('Policy saved.')).toBeInTheDocument()
    expect(screen.getByText("This company's policy applies.")).toBeInTheDocument()
  })

  it('removes the company policy', async () => {
    vi.spyOn(modelsApi, 'settings').mockResolvedValue({
      ...SETTINGS, model_policy: { bands: { high: 'strong' } }, policy_source: 'company',
    })
    const save = vi.spyOn(modelsApi, 'savePolicy').mockResolvedValue({ ...SETTINGS, policy_source: null, deployment_policy: null })
    renderPolicy()
    expect(await screen.findByLabelText('High difficulty')).toHaveValue('strong')
    fireEvent.click(screen.getByRole('button', { name: 'Remove the policy' }))
    await waitFor(() => expect(save.mock.calls[0][2]).toBeNull())
    expect(await screen.findByText('No policy: every task runs on its role\'s model.')).toBeInTheDocument()
  })

  it('is read-only for a Viewer', async () => {
    vi.spyOn(modelsApi, 'settings').mockResolvedValue(SETTINGS)
    renderPolicy(false)
    expect(await screen.findByLabelText('Medium difficulty')).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save the policy' })).not.toBeInTheDocument()
  })
})
