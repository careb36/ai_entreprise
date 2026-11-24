import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChangePassword } from './ChangePassword'
import { queryClient } from '../lib/queryClient'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, headers: new Headers(), json: async () => body } as Response
}

const ME = { user_id: 1, email: 'ana@acme.test', full_name: 'Ana', memberships: [], has_password: true, password_min_length: 12 }

function stubApi(overrides: Record<string, (init: RequestInit) => Response> = {}, me: object = ME) {
  const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    const { pathname } = new URL(url)
    const key = `${init.method ?? 'GET'} ${pathname}`
    if (overrides[key]) return overrides[key](init)
    if (key === 'GET /v1/auth/me') return response(200, me)
    return response(404, { detail: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderForm() {
  return render(
    <QueryClientProvider client={queryClient}>
      <ChangePassword />
    </QueryClientProvider>,
  )
}

async function fill(current: string, next: string, repeat = next) {
  fireEvent.change(await screen.findByLabelText('Current password'), { target: { value: current } })
  fireEvent.change(screen.getByLabelText('New password'), { target: { value: next } })
  fireEvent.change(screen.getByLabelText('Repeat the new password'), { target: { value: repeat } })
  fireEvent.click(screen.getByRole('button', { name: 'Change password' }))
}

const posts = (fetch: ReturnType<typeof stubApi>) =>
  fetch.mock.calls.filter(([url, init]) => String(url).endsWith('/v1/auth/password') && init?.method === 'POST')

describe('ChangePassword', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    queryClient.clear()
  })

  it('shows the minimum length and sends the current and new password', async () => {
    const fetch = stubApi({
      'POST /v1/auth/password': () => response(200, { status: 'changed', sessions_ended: 2, csrf_token: 'c', session_expires_in: 600 }),
    })
    renderForm()
    expect(await screen.findByText(/at least 12 characters/)).toBeInTheDocument()
    await fill('old-password', 'a-brand-new-passphrase')
    expect(await screen.findByRole('status')).toHaveTextContent(/2 sessions were ended/)
    const [call] = posts(fetch)
    expect(JSON.parse(String(call[1]?.body))).toEqual({
      current_password: 'old-password',
      new_password: 'a-brand-new-passphrase',
    })
    expect(screen.getByLabelText('Current password')).toHaveValue('')
  })

  it('refuses two different new passwords without asking the server', async () => {
    const fetch = stubApi()
    renderForm()
    await fill('old-password', 'a-brand-new-passphrase', 'a-different-passphrase')
    expect(await screen.findByRole('alert')).toHaveTextContent('The two new passwords differ.')
    expect(posts(fetch)).toHaveLength(0)
  })

  it('refuses a new password under the minimum without asking the server', async () => {
    const fetch = stubApi()
    renderForm()
    await fill('old-password', 'short')
    expect(await screen.findByRole('alert')).toHaveTextContent('at least 12 characters')
    expect(posts(fetch)).toHaveLength(0)
  })

  it("says the current password is wrong, from the server's code", async () => {
    stubApi({
      'POST /v1/auth/password': () =>
        response(403, { detail: { code: 'wrong_password', message: 'The current password is not right.' } }),
    })
    renderForm()
    await fill('not-it', 'a-brand-new-passphrase')
    expect(await screen.findByRole('alert')).toHaveTextContent('The current password is not right.')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('says to wait while the account is locked', async () => {
    stubApi({
      'POST /v1/auth/password': () => response(429, { detail: { code: 'locked', message: 'Too many attempts' } }),
    })
    renderForm()
    await fill('guess', 'a-brand-new-passphrase')
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/wait a little/))
  })

  it('tells an SSO-only account it has no password, with no form', async () => {
    stubApi({}, { ...ME, has_password: false })
    renderForm()
    expect(await screen.findByText(/no password to change/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Current password')).not.toBeInTheDocument()
  })
})
