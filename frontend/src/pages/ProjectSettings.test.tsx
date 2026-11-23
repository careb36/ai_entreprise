import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ProjectSettings, { choiceOf } from './ProjectSettings'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const stack = {
  company_id: 4,
  overrides: { test: 'pytest -q', lint: '', folders: { web: { stack: 'node' } } },
  effective_without_detection: {}, stacks: [{ stack: 'node' }, { stack: 'python' }],
  installed_manifests: { 'requirements.txt': 'abc' }, folders_without_detection: {}, every_delivery: [],
}

function routed() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const { pathname } = new URL(url)
    if (pathname === '/v1/companies/4/team_selection') {
      return response(200, { company_id: 4, team_selection: init?.method === 'PUT' ? JSON.parse(String(init.body)).team_selection : 'suggested' })
    }
    if (pathname === '/v1/companies/4/stack_profile' && init?.method === 'PUT') return response(200, { ...stack, overrides: JSON.parse(String(init.body)).profile })
    if (pathname === '/v1/companies/4/stack_profile') return response(200, stack)
    return response(404)
  })
}

function renderAs(role: 'Owner' | 'Manager' | 'Viewer') {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: role }] })
  return render(<QueryClientProvider client={queryClient}><ProjectSettings /></QueryClientProvider>)
}

const puts = (fetch: ReturnType<typeof routed>, path: string) =>
  fetch.mock.calls.filter(([url, init]) => init?.method === 'PUT' && new URL(String(url)).pathname === path).map(([, init]) => JSON.parse(String(init?.body)))

describe('ProjectSettings', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4 })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('offers the export to the Owner only', async () => {
    vi.stubGlobal('fetch', routed())
    const { unmount } = renderAs('Manager')
    await screen.findByRole('form', { name: 'Stack profile' })
    expect(screen.queryByRole('button', { name: 'Export to a file' })).not.toBeInTheDocument()
    unmount()
    renderAs('Owner')
    expect(await screen.findByRole('button', { name: 'Export to a file' })).toBeInTheDocument()
  })

  it('shows a viewer the settings read-only', async () => {
    vi.stubGlobal('fetch', routed())
    renderAs('Viewer')
    expect(await screen.findByText(/Suggested — The PO suggests/)).toBeInTheDocument()
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
    const form = await screen.findByRole('form', { name: 'Stack profile' })
    expect(within(form).getByLabelText('The Test command')).toHaveValue('pytest -q')
    expect(within(form).getByLabelText('The Test command')).toBeDisabled()
    expect(within(form).queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
  })

  it('lets a manager choose how teams are chosen', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager')
    fireEvent.click(await screen.findByRole('radio', { name: /Automatic/ }))
    await waitFor(() => expect(puts(fetch, '/v1/companies/4/team_selection')).toEqual([{ team_selection: 'automatic' }]))
  })

  it('saves the overrides, turning a step off and keeping the folders as stored', async () => {
    const fetch = routed()
    vi.stubGlobal('fetch', fetch)
    renderAs('Manager')
    const form = await screen.findByRole('form', { name: 'Stack profile' })
    expect(within(form).getByLabelText('How the Lint step is set')).toHaveValue('off')
    fireEvent.change(within(form).getByLabelText('Stack'), { target: { value: 'python' } })
    fireEvent.change(within(form).getByLabelText('Port'), { target: { value: '70000' } })
    expect(within(form).getByRole('button', { name: 'Save' })).toBeDisabled()
    fireEvent.change(within(form).getByLabelText('Port'), { target: { value: '8000' } })
    fireEvent.change(within(form).getByLabelText('How the Install step is set'), { target: { value: 'command' } })
    fireEvent.change(within(form).getByLabelText('The Install command'), { target: { value: 'pip install -r requirements.txt' } })
    fireEvent.change(within(form).getByLabelText('How the Test step is set'), { target: { value: 'detected' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(puts(fetch, '/v1/companies/4/stack_profile')).toEqual([{
      profile: { folders: { web: { stack: 'node' } }, stack: 'python', port: 8000, install: 'pip install -r requirements.txt', lint: '' },
    }]))
  })

  it('reads a stored field as detected, a command or off', () => {
    expect(choiceOf(undefined)).toEqual({ mode: 'detected', value: '' })
    expect(choiceOf('')).toEqual({ mode: 'off', value: '' })
    expect(choiceOf('make')).toEqual({ mode: 'command', value: 'make' })
  })
})
