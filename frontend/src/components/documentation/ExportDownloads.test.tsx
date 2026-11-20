import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ExportDownloads } from './ExportDownloads'
import { queryClient } from '../../lib/queryClient'

function response(status: number, body: unknown = {}, headers: Record<string, string> = {}) {
  return {
    ok: status >= 200 && status < 300, status, json: async () => body, text: async () => JSON.stringify(body),
    blob: async () => new Blob(['x']), headers: new Headers({ 'Content-Type': 'application/json', ...headers }),
  } as unknown as Response
}

const formats = (pdf: { available: boolean; reason: string | null }) => ({
  formats: { docx: { available: true, reason: null }, pdf },
})

let saved: { create: typeof URL.createObjectURL; revoke: typeof URL.revokeObjectURL }
let click: ReturnType<typeof vi.spyOn>

function renderIt() {
  return render(<QueryClientProvider client={queryClient}><ExportDownloads packId={5} /></QueryClientProvider>)
}

describe('documentation pack export', () => {
  beforeEach(() => {
    queryClient.clear()
    saved = { create: URL.createObjectURL, revoke: URL.revokeObjectURL }
    Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
    click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  })
  afterEach(() => {
    Object.assign(URL, { createObjectURL: saved.create, revokeObjectURL: saved.revoke })
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('downloads the DOCX and the PDF export manifest under the names the backend gives', async () => {
    const fetch = vi.fn(async (url: string) => {
      if (String(url).endsWith('/v1/documentation/exports')) return response(200, formats({ available: true, reason: null }))
      return response(200, {}, { 'Content-Disposition': 'attachment; filename="documentation-pack-5-v1.docx"' })
    })
    vi.stubGlobal('fetch', fetch)
    renderIt()
    fireEvent.click(screen.getByRole('button', { name: 'DOCX' }))
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1))
    expect(fetch.mock.calls.map(c => String(c[0]))).toContainEqual(expect.stringContaining('/v1/documentation/5/export/docx'))
    fireEvent.click(screen.getByRole('button', { name: 'Signed manifest of the PDF export' }))
    await waitFor(() => expect(click).toHaveBeenCalledTimes(2))
    expect(fetch.mock.calls.map(c => String(c[0]))).toContainEqual(expect.stringContaining('/v1/documentation/5/export/pdf/manifest'))
  })

  it('disables a format the server cannot render and says why', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(200, formats({ available: false, reason: 'WeasyPrint cannot load: no pango' }))))
    renderIt()
    const pdf = screen.getByRole('button', { name: 'PDF/A' })
    await waitFor(() => expect(pdf).toBeDisabled())
    expect(pdf).toHaveAttribute('title', 'Not available on this server: WeasyPrint cannot load: no pango')
    expect(screen.getByRole('button', { name: 'DOCX' })).toBeEnabled()
  })

  it('shows the backend refusal when an export fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => String(url).endsWith('/exports')
      ? response(200, formats({ available: true, reason: null }))
      : response(503, { detail: 'PDF export is unavailable here: no pango' })))
    renderIt()
    fireEvent.click(screen.getByRole('button', { name: 'PDF/A' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not export the pack')
  })
})
