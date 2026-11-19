import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import McpServers from './McpServers'
import { parseServersMap } from '../services/mcpServers'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const server = (id: number, name: string, extra: Record<string, unknown> = {}) => ({
  id, company_id: 4, name, transport: 'http', url: `https://${name}.example.com/mcp`, command: null, args: [], cwd: null,
  roles: [], enabled: true, description: null, timeout_seconds: 30, created_by: null, created_at: null, updated_at: null,
  start_refused: null, env_keys: [], header_names: ['Authorization'], secrets_readable: true, ...extra,
})
const servers = [server(1, 'docs'), server(2, 'local', { transport: 'stdio', url: null, command: 'npx', args: ['tool'], enabled: false, start_refused: 'npx is not on MCP_STDIO_ALLOWED_COMMANDS' })]

function routed() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const { pathname } = new URL(url)
    if (pathname === '/v1/mcp/servers' && init?.method === 'POST') return response(201, { servers })
    if (pathname === '/v1/mcp/servers') return response(200, { servers })
    if (pathname === '/v1/mcp/servers/1' && init?.method === 'PATCH') return response(200, servers[0])
    if (pathname === '/v1/mcp/servers/1' && init?.method === 'DELETE') return response(204)
    if (pathname === '/v1/mcp/servers/1/tools') return response(200, { server: 'docs', tools: [{ name: 'search', description: 'Search the docs' }] })
    return response(404)
  })
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(<QueryClientProvider client={queryClient}><McpServers /></QueryClientProvider>)
}

const calls = (fetch: ReturnType<typeof routed>, method: string) =>
  fetch.mock.calls.filter(([, init]) => init?.method === method).map(([url, init]) => [new URL(String(url)).pathname, init?.body ? JSON.parse(String(init.body)) : null])

describe('McpServers', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4 })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows a viewer each server, its secrets by name and a policy refusal, with no action', async () => {
    vi.stubGlobal('fetch', routed())
    renderAs('Viewer')
    const list = await screen.findByRole('list', { name: 'Servers' })
    expect(within(list).getByText('docs')).toBeInTheDocument()
    expect(within(list).getAllByText(/Secrets: Authorization/)).toHaveLength(2)
    expect(within(list).getByText(/would not start it: npx is not on/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('disables a server, scopes its roles, lists its tools and removes it after a confirmation', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager')
    const list = await screen.findByRole('list', { name: 'Servers' })
    const docs = within(list).getAllByRole('listitem')[0]
    fireEvent.click(within(docs).getByRole('button', { name: 'Disable' }))
    await waitFor(() => expect(calls(fetch, 'PATCH')).toContainEqual(['/v1/mcp/servers/1', { enabled: false }]))
    fireEvent.change(within(docs).getByLabelText('Roles (comma-separated; empty for every role)'), { target: { value: 'Developer, QA ,' } })
    fireEvent.click(within(docs).getByRole('button', { name: 'Save roles' }))
    await waitFor(() => expect(calls(fetch, 'PATCH')).toContainEqual(['/v1/mcp/servers/1', { roles: ['Developer', 'QA'] }]))
    fireEvent.click(within(docs).getByRole('button', { name: 'List tools' }))
    expect(await within(docs).findByText('search')).toBeInTheDocument()
    fireEvent.click(within(docs).getByRole('button', { name: 'Remove the server docs' }))
    fireEvent.click(within(docs).getByRole('button', { name: 'Yes, remove' }))
    await waitFor(() => expect(calls(fetch, 'DELETE')).toEqual([['/v1/mcp/servers/1', null]]))
  })

  it('will not enable a server the policy refuses', async () => {
    vi.stubGlobal('fetch', routed())
    renderAs('Manager')
    const list = await screen.findByRole('list', { name: 'Servers' })
    const local = within(list).getAllByRole('listitem')[1]
    expect(within(local).getByRole('button', { name: 'Enable' })).toBeDisabled()
    expect(within(local).getByRole('button', { name: 'List tools' })).toBeDisabled()
  })

  it('adds servers from a pasted map and clears the field, which may hold secrets', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Owner')
    const field = await screen.findByLabelText('An mcpServers map (JSON)')
    fireEvent.change(field, { target: { value: '{"mcpServers": ' } })
    expect(field).toHaveAttribute('aria-invalid', 'true')
    fireEvent.change(field, { target: { value: '{"mcpServers": {"docs": {"url": "https://docs.example.com/mcp", "headers": {"Authorization": "Bearer x"}}}}' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add servers' }))
    expect(await screen.findByText('Servers saved')).toBeInTheDocument()
    expect(field).toHaveValue('')
    expect(calls(fetch, 'POST')[0][1]).toEqual({ company_id: 4, mcpServers: { docs: { url: 'https://docs.example.com/mcp', headers: { Authorization: 'Bearer x' } } } })
  })

  it('reads a bare map or the wrapped one, and names what is wrong', () => {
    expect(parseServersMap('{"a": {}}')).toEqual({ map: { a: {} } })
    expect(parseServersMap('{"mcpServers": {"a": {}}}')).toEqual({ map: { a: {} } })
    expect(parseServersMap('[1]')).toEqual({ error: 'shape' })
    expect(parseServersMap('{}')).toEqual({ error: 'empty' })
    expect(parseServersMap('{')).toEqual({ error: 'json' })
  })
})
