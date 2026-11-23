import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CompanyExportPanel, { exportFileName } from './CompanyExportPanel'
import { queryClient } from '../lib/queryClient'
import { api, type CompanyExport } from '../services/api'
import { saveJson } from '../lib/saveJson'

vi.mock('../lib/saveJson', () => ({ saveJson: vi.fn() }))

const EXPORT: CompanyExport = {
  manifest: {
    format: 'la-fabrique.company/1',
    company_id: 4,
    exported_at: '2026-11-22T18:30:00+00:00',
    sha256: 'abc',
    sha256_of: 'content, canonical JSON (sorted keys, no spaces)',
  },
  content: { company: { company_name: 'Acme Ñandú' } },
  required_secrets: [{ kind: 'provider_api_key', connection: 'main', provider: 'deepseek' }],
  redacted: [{ where: 'agents[0].system_prompt', line: 3 }, { where: 'routines[0].description', line: 1 }],
}

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>)
}

describe("exporting the company's configuration", () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
    vi.mocked(saveJson).mockClear()
  })

  it('names the file after the company and the day', () => {
    expect(exportFileName('Acme Ñandú', EXPORT)).toBe('acme-nandu-export-2026-11-22.json')
    expect(exportFileName(null, EXPORT)).toBe('company-4-export-2026-11-22.json')
    expect(exportFileName('***', EXPORT)).toBe('company-4-export-2026-11-22.json')
  })

  it('saves what the backend returned and says what was left out', async () => {
    const exportCompany = vi.spyOn(api, 'exportCompany').mockResolvedValue(EXPORT)
    wrap(<CompanyExportPanel companyId={4} companyName="Acme Ñandú" />)

    fireEvent.click(screen.getByRole('button', { name: 'Export to a file' }))

    await waitFor(() => expect(saveJson).toHaveBeenCalledWith('acme-nandu-export-2026-11-22.json', EXPORT))
    expect(exportCompany).toHaveBeenCalledWith(4)
    expect(screen.getByText('Saved as acme-nandu-export-2026-11-22.json.')).toBeInTheDocument()
    expect(screen.getByText('1 secret was left out and is named in the file.')).toBeInTheDocument()
    expect(screen.getByText('2 lines that looked like secrets were replaced.')).toBeInTheDocument()
  })

  it("shows the server's reason and saves nothing when the export is refused", async () => {
    vi.spyOn(api, 'exportCompany').mockRejectedValue(new Error('Owner role required'))
    wrap(<CompanyExportPanel companyId={4} companyName="Acme" />)

    fireEvent.click(screen.getByRole('button', { name: 'Export to a file' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('The export failed: Owner role required')
    expect(saveJson).not.toHaveBeenCalled()
  })
})
