import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

// The scene needs a WebGL canvas and a headset; jsdom has neither. The room's
// model, layout and live adapters are tested on their own; this checks the
// HTML shell and how the two modes start.
vi.mock('@react-three/fiber', () => ({ Canvas: () => null, useFrame: () => {} }))
vi.mock('@react-three/drei', () => ({ OrbitControls: () => null, Text: () => null, Grid: () => null }))
vi.mock('@react-three/xr', () => ({
  VRButton: () => <button type="button">Enter VR</button>,
  XR: ({ children }: { children?: unknown }) => <>{children as never}</>,
  Hands: () => null,
  Controllers: () => null,
  Interactive: ({ children }: { children?: unknown }) => <>{children as never}</>,
  useXR: () => false,
}))

function at(search: string) {
  window.history.replaceState({}, '', `/${search}`)
}

describe('Glassroom VR', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    at('')
  })

  it('opens on the demo company with what waits for the person, and offers to enter VR', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: 'Glassroom VR' })).toBeInTheDocument()
    expect(screen.getByText('Acme Payments · Sprint 3 — Checkout · demo company')).toBeInTheDocument()
    expect(screen.getByText(/^4 waiting for you/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enter VR' })).toBeInTheDocument()
  })

  it('keeps the demo company working for its first minute', () => {
    vi.useFakeTimers()
    try {
      render(<App />)
      expect(screen.getByText(/^4 waiting for you/)).toBeInTheDocument()
      act(() => { vi.advanceTimersByTime(61_000) })
      // A run arrived in the inbox meanwhile.
      expect(screen.getByText(/^5 waiting for you/)).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('reads a company from the backend named in the URL, and says when it cannot', async () => {
    at('?api=https://api.test&company=4')
    const answers: Record<string, unknown> = {
      '/v1/sprints?company_id=4': [{ id: 7, sprint_name: 'Sprint 7 — Live', status: 'active' }],
      '/v1/sprints/7/board': { sprint: { id: 7, sprint_name: 'Sprint 7 — Live', status: 'active' }, columns: { blocked: [{ id: 1, title: 'Stuck', status: 'blocked', assignee: 'QA', comments: [] }] } },
      '/v1/inbox?company_id=4': { blocked_tasks: [{ id: 1, reason: 'Gate', sprint_id: 7 }] },
      '/v1/agent_runs?company_id=4&exclude_in_house=true&limit=100': { agent_runs: [] },
      '/v1/sprints/7/stream_ticket': { ticket: null, expires_in: 0 },
    }
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      const path = url.replace('https://api.test', '')
      return { ok: path in answers, status: path in answers ? 200 : 404, json: async () => answers[path] } as Response
    }))
    vi.stubGlobal('EventSource', class { onmessage: unknown = null; close() {} })
    render(<App />)

    expect(screen.getByText(/connecting to the backend/)).toBeInTheDocument()
    expect(await screen.findByText('Company 4 · Sprint 7 — Live · live')).toBeInTheDocument()
    expect(screen.getByText(/^1 waiting for you/)).toBeInTheDocument()
  })

  it('keeps the demo company when the backend cannot be read', async () => {
    at('?api=https://down.test&company=4')
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 503, json: async () => ({}) }) as Response))
    render(<App />)
    await waitFor(() => expect(screen.getByText(/the backend could not be read; showing the demo company/)).toBeInTheDocument())
    expect(screen.getByText(/^4 waiting for you/)).toBeInTheDocument()
  })
})
