import { StrictMode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { LiveConfig } from './live'
import type { Action } from './model'
import { RELOAD_MS, useLiveWorld } from './useLiveWorld'

const CONFIG: LiveConfig = { api: 'https://api.test', companyId: 4, person: 'Ana' }

function stubFetch(approve = 200, hold?: { url: string; after: number; gate: Promise<void> }) {
  let calls = 0
  const answers: Record<string, [number, unknown]> = {
    '/v1/sprints?company_id=4': [200, [{ id: 7, sprint_name: 'Sprint 7', status: 'active' }]],
    '/v1/sprints/7/board': [200, { sprint: { id: 7, sprint_name: 'Sprint 7', status: 'active' }, columns: {} }],
    '/v1/inbox?company_id=4': [200, { blocked_tasks: [] }],
    '/v1/agent_runs?company_id=4&exclude_in_house=true&limit=100': [200, { agent_runs: [{ id: 10, producer: 'github', external_ref: 'pr-1', status: 'verified', latest_approval: null }] }],
    '/v1/agent_runs/10/verifications?company_id=4': [200, { verifications: [] }],
    '/v1/agent_runs/10/approve': [approve, approve === 200 ? { id: 1 } : { detail: 'Requires Manager role' }],
  }
  const fetchImpl = vi.fn(async (url: string) => {
    const path = url.replace('https://api.test', '')
    // One request held back, to make a reload sit in flight while a decision lands.
    if (hold && path === hold.url && ++calls > hold.after) await hold.gate
    const [status, body] = answers[path] ?? [404, {}]
    return { ok: status < 300, status, json: async () => body } as Response
  })
  vi.stubGlobal('fetch', fetchImpl)
  return fetchImpl
}

const loads = (dispatch: ReturnType<typeof vi.fn>) => dispatch.mock.calls.map(([action]) => action as Action).filter(action => action.type === 'load')

describe('useLiveWorld', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('loads once per mount under StrictMode, and a reload brings only the board and the runs', async () => {
    vi.useFakeTimers()
    const fetchImpl = stubFetch()
    const dispatch = vi.fn()
    renderHook(() => useLiveWorld(CONFIG, dispatch), { wrapper: StrictMode })
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })

    const sprintReads = () => fetchImpl.mock.calls.filter(([url]) => String(url).includes('/v1/sprints?')).length
    // StrictMode runs the effect twice; the first run's load is discarded with its interval.
    expect(sprintReads()).toBe(2)
    expect(loads(dispatch)).toHaveLength(1)
    expect(loads(dispatch)[0]).toMatchObject({ world: { mode: 'live', sprint: 'Sprint 7' } })
    expect((loads(dispatch)[0] as { world: object }).world).toHaveProperty('log')

    await act(async () => { await vi.advanceTimersByTimeAsync(RELOAD_MS) })
    expect(sprintReads()).toBe(3)
    expect(loads(dispatch)).toHaveLength(2)
    const reloaded = (loads(dispatch)[1] as { world: object }).world
    expect(reloaded).not.toHaveProperty('log')
    expect(reloaded).not.toHaveProperty('agents')
  })

  it('drops a reload that was in flight when a decision was recorded, so the decided run does not come back', async () => {
    vi.useFakeTimers()
    let release!: () => void
    const gate = new Promise<void>(resolve => { release = resolve })
    stubFetch(200, { url: '/v1/agent_runs?company_id=4&exclude_in_house=true&limit=100', after: 1, gate })
    const dispatch = vi.fn()
    const { result } = renderHook(() => useLiveWorld(CONFIG, dispatch))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(loads(dispatch)).toHaveLength(1)

    // The reload starts and hangs on the runs; meanwhile the person decides.
    await act(async () => { await vi.advanceTimersByTimeAsync(RELOAD_MS) })
    act(() => result.current.perform({ type: 'decide_run', id: 10, decision: 'approved', by: 'Ana' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(dispatch).toHaveBeenCalledWith({ type: 'decide_run', id: 10, decision: 'approved', by: 'Ana' })

    // The stale reload resolves: nothing is loaded from it.
    release()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(loads(dispatch)).toHaveLength(1)
    // The next reload, started after the decision, is taken.
    await act(async () => { await vi.advanceTimersByTimeAsync(RELOAD_MS) })
    expect(loads(dispatch)).toHaveLength(2)
  })

  it('shows a decision once the backend recorded it, and says so when it refused', async () => {
    stubFetch(403)
    const dispatch = vi.fn()
    const { result } = renderHook(() => useLiveWorld(CONFIG, dispatch))
    await waitFor(() => expect(result.current.status).toBe('live'))

    act(() => result.current.perform({ type: 'decide_run', id: 10, decision: 'approved', by: 'Ana' }))
    await waitFor(() => expect(dispatch).toHaveBeenCalledWith({ type: 'event', text: 'The backend did not record that: POST /v1/agent_runs/10/approve → 403' }))
    expect(dispatch).not.toHaveBeenCalledWith(expect.objectContaining({ type: 'decide_run' }))

    // Selecting a card never waits for the backend.
    act(() => result.current.perform({ type: 'select', selection: { kind: 'run', id: 10 } }))
    expect(dispatch).toHaveBeenCalledWith({ type: 'select', selection: { kind: 'run', id: 10 } })
  })

  it('records an accepted decision', async () => {
    stubFetch(200)
    const dispatch = vi.fn()
    const { result } = renderHook(() => useLiveWorld(CONFIG, dispatch))
    await waitFor(() => expect(result.current.status).toBe('live'))

    act(() => result.current.perform({ type: 'decide_run', id: 10, decision: 'approved', by: 'Ana' }))
    await waitFor(() => expect(dispatch).toHaveBeenCalledWith({ type: 'decide_run', id: 10, decision: 'approved', by: 'Ana' }))
  })

  it('stays in demo mode without a configuration', () => {
    const dispatch = vi.fn()
    const { result } = renderHook(() => useLiveWorld(null, dispatch))
    expect(result.current.status).toBe('demo')
    act(() => result.current.perform({ type: 'send_back', id: 36, by: 'You' }))
    expect(dispatch).toHaveBeenCalledWith({ type: 'send_back', id: 36, by: 'You' })
  })
})
