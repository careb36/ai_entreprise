import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ModelCatalog from './ModelCatalog'
import { queryClient } from '../lib/queryClient'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const providers = { origin: 'https://models.dev', providers: [{ id: 'acme', name: 'Acme AI', base_url: 'https://api.acme.example/v1', model_count: 2 }] }
const entry = {
  provider: 'acme', name: 'Acme AI', base_url: 'https://api.acme.example/v1',
  models: [
    { id: 'acme-large', name: 'Acme Large', tool_call: true, reasoning: false, context: 128000, cost_input: 1.5, cost_output: 6 },
    { id: 'acme-small', name: 'Acme Small' },
  ],
}

function routed() {
  return vi.fn(async (url: string) => {
    const { pathname } = new URL(url)
    if (pathname === '/v1/models/catalog') return response(200, providers)
    if (pathname === '/v1/models/catalog/acme') return response(200, entry)
    return response(404)
  })
}

function renderPage() {
  return render(<QueryClientProvider client={queryClient}><ModelCatalog /></QueryClientProvider>)
}

describe('ModelCatalog', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    queryClient.clear()
  })

  it('reads a provider only once chosen, then shows its models with capabilities as words, limits and prices', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderPage()
    const select = await screen.findByLabelText('Provider')
    expect(screen.getByText('Choose a provider to see its models.')).toBeInTheDocument()
    expect(fetch.mock.calls.some(([url]) => String(url).includes('/catalog/acme'))).toBe(false)
    fireEvent.change(select, { target: { value: 'acme' } })
    const table = await screen.findByRole('table', { name: 'Models of Acme AI' })
    const [large, small] = within(table).getAllByRole('row').slice(1)
    expect(within(large).getByText('Tools: yes')).toBeInTheDocument()
    expect(within(large).getByText('Reasoning: no')).toBeInTheDocument()
    expect(within(large).getByText('128,000')).toBeInTheDocument()
    expect(within(large).getByText('$1.50 in, $6.00 out')).toBeInTheDocument()
    expect(within(small).getByText('Tools: not recorded')).toBeInTheDocument()
    expect(screen.getByText(/Only a validated connection on the Models page/)).toBeInTheDocument()
  })

  it('filters the models by name or id', async () => {
    vi.stubGlobal('fetch', routed())
    renderPage()
    fireEvent.change(await screen.findByLabelText('Provider'), { target: { value: 'acme' } })
    await screen.findByRole('table', { name: 'Models of Acme AI' })
    fireEvent.change(screen.getByLabelText('Filter models'), { target: { value: 'small' } })
    const table = screen.getByRole('table', { name: 'Models of Acme AI' })
    expect(within(table).getAllByRole('row')).toHaveLength(2)
    expect(within(table).getByText('Acme Small')).toBeInTheDocument()
  })
})
