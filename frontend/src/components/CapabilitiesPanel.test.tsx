import { render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CapabilitiesPanel from './CapabilitiesPanel'
import { queryClient } from '../lib/queryClient'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const DEEP = {
  status: 'healthy',
  components: {},
  capabilities: {
    'llm.embeddings': {
      state: 'degraded',
      provider: 'local',
      reason: 'no embedding provider chosen (DeepSeek has no embeddings endpoint)',
    },
    knowledge_graph: { state: 'off', reason: 'NEO4J_ENABLED=false: the graph is not recorded' },
    'llm.providers': { state: 'on', reason: 'configured: deepseek' },
  },
}

function renderPanel(body: unknown, status = 200) {
  vi.stubGlobal('fetch', vi.fn(async () => response(status, body)))
  return render(
    <QueryClientProvider client={queryClient}>
      <CapabilitiesPanel />
    </QueryClientProvider>,
  )
}

describe('CapabilitiesPanel', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    queryClient.clear()
  })

  it('lists each capability with its state and the reason', async () => {
    renderPanel(DEEP)

    const panel = await screen.findByRole('region', { name: 'Capabilities' })
    const row = (await within(panel).findByText('llm.embeddings')).closest('tr')!
    expect(within(row).getByText('degraded')).toBeInTheDocument()
    expect(within(row).getByText(/DeepSeek has no embeddings endpoint/)).toBeInTheDocument()
    const graph = within(panel).getByText('knowledge_graph').closest('tr')!
    expect(within(graph).getByText('off')).toBeInTheDocument()
    expect(within(panel).getAllByRole('row').map(r => r.textContent)[1]).toContain('knowledge_graph')
  })

  it('says so when the capabilities cannot be read', async () => {
    renderPanel({}, 404)

    expect(await screen.findByText('Could not read the capabilities.')).toBeInTheDocument()
  })
})
