import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Models from './Models'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const CONNECTION = {
  id: 7, company_id: 4, provider: 'openrouter', name: 'acme-router', base_url: 'https://openrouter.ai/api/v1',
  api_key_set: true, header_names: [], secrets_readable: true, enabled: true, validated_at: '2026-11-08T00:00:00',
  validation_ok: true, validation_detail: 'listed 2 models', listed_models: ['coder', 'thinker'],
}
const ROLES = ['PO', 'Architect', 'ScrumMaster', 'Developer', 'QA', 'SecOps', 'TechWriter', 'Reviewer']
const SETTINGS = {
  default_model: null,
  role_models: { QA: 'conn:7:coder' },
  roles: ROLES,
  effective: Object.fromEntries(ROLES.map(role => [role, role === 'QA'
    ? { model: 'conn:7:coder', source: 'role' }
    : { model: 'deepseek-v4-pro', source: 'deployment' }])),
}
const SUGGESTION = { model: 'conn:7:thinker', name: 'Thinker', connection: 'acme-router', reason: 'reasons and calls tools' }

function stubApi(overrides: Record<string, (init: RequestInit) => Response> = {}) {
  const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    const { pathname } = new URL(url)
    const key = `${init.method ?? 'GET'} ${pathname}`
    if (overrides[key]) return overrides[key](init)
    if (key === 'GET /v1/companies/4/providers') return response(200, { providers: [CONNECTION], known_providers: ['openrouter'] })
    if (key === 'GET /v1/models/catalog') return response(200, { origin: 'snapshot', providers: [
      { id: 'openrouter', name: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1', model_count: 300 },
      { id: 'deepseek', name: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', model_count: 3 },
    ] })
    if (key === 'GET /v1/companies/4/models') return response(200, { origin: 'snapshot', connections: [{
      connection_id: 7, name: 'acme-router', provider: 'openrouter', validation_ok: true, models: [
        { id: 'coder', name: 'Coder', in_catalog: true, listed: true },
        { id: 'thinker', name: 'Thinker', in_catalog: true, listed: true },
        { id: 'unlisted', name: 'Not served', in_catalog: true, listed: false },
      ],
    }, {
      connection_id: 9, name: 'unchecked', provider: 'groq', validation_ok: null, models: [
        { id: 'llama', name: 'Llama', in_catalog: true, listed: false },
      ],
    }] })
    if (key === 'GET /v1/companies/4/model_settings') return response(200, SETTINGS)
    if (key === 'GET /v1/companies/4/model_suggestions') return response(200, {
      candidates: 2, suggestions: Object.fromEntries(ROLES.map(role => [role, role === 'Architect' ? SUGGESTION : null])),
    })
    return response(404)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({ token: 'valid-token', expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(
    <QueryClientProvider client={queryClient}>
      <Models />
    </QueryClientProvider>,
  )
}

describe('Models', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4 })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows a Viewer the connections and the models per role, with nothing to change', async () => {
    stubApi()
    renderAs('Viewer')

    const connections = await screen.findByRole('region', { name: 'Provider connections' })
    expect(await within(connections).findByText('acme-router')).toBeInTheDocument()
    expect(within(connections).getByText('Validated: the provider listed 2 models')).toBeInTheDocument()
    expect(within(connections).queryByRole('button', { name: 'Validate' })).not.toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Connect a provider' })).not.toBeInTheDocument()

    const choices = await screen.findByRole('region', { name: 'Models per role' })
    const qa = await within(choices).findByLabelText('QA')
    expect(qa).toBeDisabled()
    expect(qa).toHaveValue('conn:7:coder')
    expect(within(choices).getByText('chosen for this role')).toBeInTheDocument()
    expect(within(choices).queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
  })

  it('offers only what a validated provider listed, with the suggestion apart', async () => {
    stubApi()
    renderAs('Manager')

    const architect = await screen.findByLabelText('Architect')
    const groups = within(architect).getAllByRole('group')
    expect(groups.map(g => g.getAttribute('label'))).toEqual(['Suggested', 'Others'])
    expect(within(groups[0]).getByRole('option')).toHaveValue('conn:7:thinker')
    expect(within(architect).queryByRole('option', { name: 'acme-router · Not served' })).not.toBeInTheDocument()
    // A connection nobody validated offers nothing: the catalogue alone is not the provider's word.
    expect(within(architect).queryByRole('option', { name: 'unchecked · Llama' })).not.toBeInTheDocument()
    expect(screen.getByText('Suggested: acme-router · Thinker')).toBeInTheDocument()
  })

  it("says when the company's own record decided a suggestion", async () => {
    const measured = { ...SUGGESTION, model: 'conn:7:coder', name: 'Coder', measured: { tasks: 4, done: 3, rework: 2 } }
    stubApi({
      'GET /v1/companies/4/model_suggestions': () => response(200, {
        candidates: 2,
        suggestions: Object.fromEntries(ROLES.map(role => [role,
          role === 'Architect' ? SUGGESTION : role === 'Developer' ? measured : null])),
      }),
    })
    renderAs('Viewer')

    expect(await screen.findByText("Measured on 4 of the company's tasks: 3 done, 0.5 rework per task")).toBeInTheDocument()
    // The rule's suggestion, with no record, says nothing of the kind.
    expect(screen.getAllByText(/Measured on/)).toHaveLength(1)
  })

  it('lets a Manager choose models, use the suggestions and save them', async () => {
    const fetch = stubApi({
      'PUT /v1/companies/4/model_settings': init => response(200, { ...SETTINGS, ...JSON.parse(String(init.body)) }),
    })
    renderAs('Manager')

    fireEvent.change(await screen.findByLabelText('Company default'), { target: { value: 'conn:7:coder' } })
    fireEvent.click(screen.getByRole('button', { name: /Use the suggestions/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Saved.'))
    const put = fetch.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'PUT')
    expect(JSON.parse(String((put?.[1] as RequestInit).body))).toEqual({
      default_model: 'conn:7:coder',
      role_models: { QA: 'conn:7:coder', Architect: 'conn:7:thinker' },
    })
  })

  it('lets a Manager connect, validate and remove a provider, and shows a refusal', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetch = stubApi({
      'POST /v1/companies/4/providers': init => JSON.parse(String(init.body)).provider === 'bad id!'
        ? response(422, { detail: 'provider must be a models.dev id' })
        : response(201, { ...CONNECTION, id: 8, name: 'deepseek', provider: 'deepseek' }),
      'POST /v1/companies/4/providers/7/validate': () => response(200, { ok: true, provider: CONNECTION }),
      'DELETE /v1/companies/4/providers/7': () => response(204),
    })
    renderAs('Manager')

    const form = await screen.findByRole('form', { name: 'Connect a provider' })
    fireEvent.change(within(form).getByLabelText('Provider'), { target: { value: 'bad id!' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Connect' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('provider must be a models.dev id')

    fireEvent.change(within(form).getByLabelText('Provider'), { target: { value: 'deepseek' } })
    expect(within(form).getByLabelText('Base URL')).toHaveAttribute('placeholder', 'https://api.deepseek.com/v1')
    fireEvent.change(within(form).getByLabelText('API key'), { target: { value: 'sk-secret' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Connect' }))
    await waitFor(() => expect(within(form).getByLabelText('API key')).toHaveValue(''))
    const created = fetch.mock.calls.filter(([url, init]) =>
      String(url).endsWith('/providers') && (init as RequestInit)?.method === 'POST').pop()
    expect(JSON.parse(String((created?.[1] as RequestInit).body))).toEqual({ provider: 'deepseek', api_key: 'sk-secret' })

    fireEvent.click(screen.getByRole('button', { name: 'Validate' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove acme-router' }))
    await waitFor(() => {
      const methods = fetch.mock.calls.map(([url, init]) => `${(init as RequestInit)?.method ?? 'GET'} ${new URL(String(url)).pathname}`)
      expect(methods).toContain('POST /v1/companies/4/providers/7/validate')
      expect(methods).toContain('DELETE /v1/companies/4/providers/7')
    })
  })
})
