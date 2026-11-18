import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api'
import { personSectionsApi } from './documentation'
import { useAuthStore } from '../store/useAuthStore'
import { personSections, statement } from '../test/documentationFixtures'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

function call(fetch: ReturnType<typeof vi.fn>, index = 0) {
  const [url, init] = fetch.mock.calls[index] as [string, RequestInit]
  return { path: new URL(url).pathname, method: init.method ?? 'GET', body: init.body }
}

describe('person sections API', () => {
  beforeEach(() => {
    useAuthStore.setState({ expiresAt: Date.now() + 60_000 })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
  })

  it('reads the sections and one section history', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(response(200, personSections()))
      .mockResolvedValueOnce(response(200, { section: 'charter.budget', revisions: [statement()] }))
    vi.stubGlobal('fetch', fetch)

    const list = await personSectionsApi.list(4)
    expect(list.sections).toHaveLength(7)
    expect(call(fetch)).toMatchObject({ path: '/v1/companies/4/documentation/person-sections', method: 'GET' })

    const history = await personSectionsApi.history(4, 'charter.budget')
    expect(history.revisions[0].author_role).toBe('Owner')
    expect(call(fetch, 1)).toMatchObject({ path: '/v1/companies/4/documentation/person-sections/charter.budget/history', method: 'GET' })
  })

  it('sends only the text when writing: no author, time or digest', async () => {
    const fetch = vi.fn().mockResolvedValue(response(200, statement()))
    vi.stubGlobal('fetch', fetch)

    await personSectionsApi.write(4, 'charter.budget', 'Forty thousand.')
    const sent = call(fetch)
    expect(sent).toMatchObject({ path: '/v1/companies/4/documentation/person-sections/charter.budget', method: 'PUT' })
    expect(JSON.parse(String(sent.body))).toEqual({ text: 'Forty thousand.' })
  })

  it('withdraws with a DELETE and keeps the refusal', async () => {
    const fetch = vi.fn().mockResolvedValue(response(409, { detail: 'The section has no statement to withdraw' }))
    vi.stubGlobal('fetch', fetch)

    const error = await personSectionsApi.withdraw(4, 'risks.appetite').catch(e => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(409)
    expect(call(fetch)).toMatchObject({ path: '/v1/companies/4/documentation/person-sections/risks.appetite', method: 'DELETE' })
  })

  it('never resends a write that failed on the server: each one is an audited revision', async () => {
    const fetch = vi.fn().mockResolvedValue(response(500, { detail: 'boom' }))
    vi.stubGlobal('fetch', fetch)

    await expect(personSectionsApi.write(4, 'charter.budget', 'x')).rejects.toBeInstanceOf(ApiError)
    await expect(personSectionsApi.withdraw(4, 'charter.budget')).rejects.toBeInstanceOf(ApiError)
    expect(fetch).toHaveBeenCalledTimes(2)
  })
})
