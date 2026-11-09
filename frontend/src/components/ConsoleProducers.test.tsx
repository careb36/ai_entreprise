import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ConsoleProducers from './ConsoleProducers'
import { queryClient } from '../lib/queryClient'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const CLIS = [
  { id: 'claude', label: 'Claude Code', command: 'claude -p --permission-mode bypassPermissions', cooldown_until: null },
  { id: 'codex', label: 'OpenAI Codex CLI', command: 'codex exec', cooldown_until: '2026-11-09T12:00:00+00:00' },
]
const SETTINGS = {
  company_id: 4,
  producers: { Developer: 'claude' },
  roles: ['Developer', 'QA', 'SecOps', 'TechWriter'],
  clis: CLIS,
  git_workflow: true,
  sandbox: { mode: 'container', network: true },
  warnings: [] as string[],
}

function stubApi(settings: object = SETTINGS, put?: (init: RequestInit) => Response) {
  const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    const { pathname } = new URL(url)
    const key = `${init.method ?? 'GET'} ${pathname}`
    if (key === 'GET /v1/companies/4/console_producers') return response(200, settings)
    if (key === 'PUT /v1/companies/4/console_producers' && put) return put(init)
    return response(404)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderAs(canManage: boolean) {
  useAuthStore.setState({ token: 'valid-token', expiresAt: Date.now() + 60_000, memberships: [] })
  return render(
    <QueryClientProvider client={queryClient}>
      <ConsoleProducers companyId={4} canManage={canManage} />
    </QueryClientProvider>,
  )
}

describe('ConsoleProducers', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('lets a Manager name the CLI that produces a role and saves only the chosen ones', async () => {
    const put = vi.fn((init: RequestInit) => response(200, { ...SETTINGS, producers: JSON.parse(String(init.body)).producers }))
    stubApi(SETTINGS, put)
    renderAs(true)
    const developer = await screen.findByLabelText('Developer')
    expect((developer as HTMLSelectElement).value).toBe('claude')
    expect(screen.getByText(/OpenAI Codex CLI is cooling down/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('QA'), { target: { value: 'codex' } })
    fireEvent.change(developer, { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(put).toHaveBeenCalled())
    expect(JSON.parse(String(put.mock.calls[0][0].body))).toEqual({ producers: { QA: 'codex' } })
    expect(await screen.findByText('Console producers saved.')).toBeInTheDocument()
  })

  it('shows a Viewer the choices with nothing to change', async () => {
    stubApi()
    renderAs(false)
    const developer = await screen.findByLabelText('Developer')
    expect(developer).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
  })

  it('asks for the Git workflow first and shows the refusal', async () => {
    stubApi({ ...SETTINGS, producers: {}, git_workflow: false })
    renderAs(true)
    expect(await screen.findByText(/turn the Git workflow on first/)).toBeInTheDocument()
    expect(screen.getByLabelText('Developer')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('warns that no CLI runs on the host sandbox', async () => {
    stubApi({ ...SETTINGS, sandbox: { mode: 'host', network: true }, warnings: ['console producers need a container or bwrap sandbox'] })
    renderAs(true)
    expect(await screen.findByRole('note')).toHaveTextContent('No CLI runs until then: console producers need a container or bwrap sandbox')
  })

  it('shows the backend refusing a save', async () => {
    stubApi(SETTINGS, () => response(422, { detail: 'not a console producer: x' }))
    renderAs(true)
    await screen.findByLabelText('Developer')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('not a console producer: x')
  })
})
