import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ExecutionSettings, { splitList } from './ExecutionSettings'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const sandbox = {
  company_id: 4, mode: null, effective_mode: 'host', default_mode: 'host', env: ['CI'],
  limits: { image: 'python:3.12-slim', network: false, cpus: 2, memory_mb: 2048, pids_limit: 256 }, cache: {},
}
const research = { company_id: 4, enabled: true, roles: ['Architect'], hosts: ['docs.example.com'], default_hosts: ['pypi.org'] }

function routed() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const { pathname } = new URL(url)
    if (pathname === '/v1/companies/4/sandbox' && init?.method === 'PUT') {
      const body = JSON.parse(String(init.body))
      return response(200, { ...sandbox, mode: body.mode, effective_mode: body.mode ?? 'host', env: body.env })
    }
    if (pathname === '/v1/companies/4/sandbox') return response(200, sandbox)
    if (pathname === '/v1/companies/4/sandbox/cache/reset') return response(200, { volume_reset: {}, quarantine_cleared: true })
    if (pathname === '/v1/companies/4/research' && init?.method === 'PUT') return response(200, { ...research, hosts: JSON.parse(String(init.body)).hosts })
    if (pathname === '/v1/companies/4/research') return response(200, research)
    return response(404)
  })
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(<QueryClientProvider client={queryClient}><ExecutionSettings /></QueryClientProvider>)
}

const puts = (fetch: ReturnType<typeof routed>, path: string) =>
  fetch.mock.calls.filter(([url, init]) => init?.method === 'PUT' && new URL(String(url)).pathname === path).map(([, init]) => JSON.parse(String(init?.body)))

describe('ExecutionSettings', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4 })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows a viewer the settings in force, warns about the host, and offers no change', async () => {
    vi.stubGlobal('fetch', routed())
    renderAs('Viewer')
    expect(await screen.findByText(/Agent-written code runs in: the host, no isolation/)).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent('without isolation')
    expect(await screen.findByText(/The company adds: docs.example.com/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reset the dependency cache' })).not.toBeInTheDocument()
  })

  it('saves the sandbox mode and variables, refusing a bad name first', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager')
    const form = await screen.findByRole('form', { name: 'Sandbox' })
    fireEvent.change(within(form).getByLabelText('Mode'), { target: { value: 'container' } })
    const env = within(form).getByLabelText('Variables a sandboxed run sees (names only)')
    fireEvent.change(env, { target: { value: 'CI\n9BAD' } })
    expect(env).toHaveAttribute('aria-invalid', 'true')
    expect(within(form).getByRole('button', { name: 'Save' })).toBeDisabled()
    fireEvent.change(env, { target: { value: 'CI\nNODE_ENV, CI' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(puts(fetch, '/v1/companies/4/sandbox')).toEqual([{ mode: 'container', env: ['CI', 'NODE_ENV'] }]))
  })

  it('asks before resetting the cache', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    fireEvent.click(await screen.findByRole('button', { name: 'Reset the dependency cache' }))
    expect(fetch.mock.calls.some(([url]) => String(url).includes('cache/reset'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Yes, reset' }))
    expect(await screen.findByText('The cache was reset and its quarantine lifted')).toBeInTheDocument()
  })

  it('saves the research hosts in lower case, refusing what is not a host name', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager')
    const form = await screen.findByRole('form', { name: 'Research sources' })
    const field = within(form).getByLabelText('Hosts the company adds')
    fireEvent.change(field, { target: { value: 'https://bad/path' } })
    expect(field).toHaveAttribute('aria-invalid', 'true')
    fireEvent.change(field, { target: { value: 'Docs.Example.com\nwiki.example.org' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(puts(fetch, '/v1/companies/4/research')).toEqual([{ hosts: ['docs.example.com', 'wiki.example.org'] }]))
  })

  it('splits a list on lines and commas, without blanks or repeats', () => {
    expect(splitList(' A,\nB\n\nA , C ')).toEqual(['A', 'B', 'C'])
  })
})
