import { useState, type ChangeEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { AlertTriangle, FileUp } from 'lucide-react'
import { useCompanyImport } from '../../hooks/useCompanyImport'
import { errorDetail, type CompanyImportResult, type ImportToSetUp } from '../../services/api'

const MAX_BYTES = 5_000_000
const ACCEPT = '.json,application/json'

interface Picked {
  fileName: string
  file: Record<string, unknown>
}

/** The export file's own company name, to suggest as the new one. */
function nameIn(file: Record<string, unknown>): string {
  const content = file.content as { company?: { company_name?: unknown } } | undefined
  const name = content?.company?.company_name
  return typeof name === 'string' ? name : ''
}

/** Reads the picked file; a reason key when it is not an export file. */
async function read(file: File): Promise<Record<string, unknown> | string> {
  if (file.size > MAX_BYTES) return 'companyImport.tooLarge'
  let parsed: unknown
  try {
    parsed = JSON.parse(await file.text())
  } catch {
    return 'companyImport.notJson'
  }
  const content = (parsed as { content?: unknown } | null)?.content
  if (!parsed || typeof parsed !== 'object' || !content || typeof content !== 'object') {
    return 'companyImport.notAnExport'
  }
  return parsed as Record<string, unknown>
}

function setUpLine(item: ImportToSetUp, t: TFunction): string {
  if (item.provider_connection !== undefined) {
    return t('companyImport.setUp.connection', { name: item.provider_connection, provider: item.provider ?? '' })
  }
  switch (item.kind) {
    case 'provider_api_key':
      return t('companyImport.setUp.apiKey', { name: item.connection ?? '', provider: item.provider ?? '' })
    case 'provider_headers':
      return t('companyImport.setUp.headers', { name: item.connection ?? '', provider: item.provider ?? '' })
    case 'project_credential':
      return t('companyImport.setUp.credential', { type: item.type ?? '', access: item.access ?? '' })
    case 'environment_variable':
      return t('companyImport.setUp.environment', { name: item.name ?? '', scope: item.for ?? '' })
    default:
      return JSON.stringify(item)
  }
}

function Result({ result, onOpen }: { result: CompanyImportResult; onOpen: () => void }) {
  const { t } = useTranslation()
  return (
    <div className="space-y-3" role="status">
      <p className="text-sm text-gray-200">
        {t('companyImport.created', {
          name: result.company_name,
          agents: result.created.agents,
          units: result.created.business_units,
          routines: result.created.routines,
        })}
      </p>
      {result.routines_off && <p className="text-xs text-gray-400">{t('companyImport.routinesOff')}</p>}
      {!result.digest_matches && (
        <p className="flex items-start gap-1.5 text-xs text-amber-300">
          <AlertTriangle size={13} aria-hidden="true" className="mt-0.5 shrink-0" />
          {t('companyImport.digestMismatch')}
        </p>
      )}
      {result.to_set_up.length > 0 && (
        <section className="space-y-1">
          <h4 className="text-xs font-semibold text-gray-200">{t('companyImport.toSetUp')}</h4>
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-gray-300">
            {result.to_set_up.map((item, i) => <li key={i}>{setUpLine(item, t)}</li>)}
          </ul>
        </section>
      )}
      {result.skipped.length > 0 && (
        <section className="space-y-1">
          <h4 className="text-xs font-semibold text-gray-200">{t('companyImport.skipped')}</h4>
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-gray-300">
            {result.skipped.map((item, i) => (
              <li key={i}><code>{item.what}</code>: {item.why}</li>
            ))}
          </ul>
        </section>
      )}
      <button type="button" className="btn-primary" onClick={onOpen}>{t('companyImport.open')}</button>
    </div>
  )
}

/**
 * Start a company from a file made by the export (issue #774). The backend
 * checks each value as its own route would, never sets from a file what runs
 * on the machine, and lists what the Owner still has to set up.
 */
export function ImportCompany({ onOpen }: { onOpen: (companyId: number, name: string) => void }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [picked, setPicked] = useState<Picked | null>(null)
  const [name, setName] = useState('')
  const [readError, setReadError] = useState<string | null>(null)
  const importing = useCompanyImport()

  const pick = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    setPicked(null)
    setReadError(null)
    importing.reset()
    if (!file) return
    const outcome = await read(file)
    if (typeof outcome === 'string') {
      setReadError(outcome)
      return
    }
    setPicked({ fileName: file.name, file: outcome })
    setName(nameIn(outcome))
  }

  const submit = () => {
    if (picked) importing.mutate({ file: picked.file, companyName: name })
  }

  if (!open) {
    return (
      <button type="button" className="btn-secondary flex items-center gap-1.5" onClick={() => setOpen(true)}>
        <FileUp size={14} aria-hidden="true" />
        {t('companyImport.start')}
      </button>
    )
  }

  const result = importing.data
  return (
    <section aria-labelledby="company-import-title" className="panel space-y-3 p-4">
      <h2 id="company-import-title" className="flex items-center gap-1.5 text-sm font-semibold text-gray-100">
        <FileUp size={14} aria-hidden="true" />
        {t('companyImport.title')}
      </h2>
      {result ? (
        <Result result={result} onOpen={() => onOpen(result.company_id, result.company_name)} />
      ) : (
        <>
          <p className="text-xs text-gray-400">{t('companyImport.hint')}</p>
          <div className="space-y-1">
            <label htmlFor="company-import-file" className="block text-xs text-gray-300">
              {t('companyImport.file')}
            </label>
            <input id="company-import-file" type="file" accept={ACCEPT} onChange={pick} className="text-xs" />
          </div>
          {readError && <p className="text-xs text-red-400" role="alert">{t(readError)}</p>}
          {picked && (
            <div className="space-y-1">
              <label htmlFor="company-import-name" className="block text-xs text-gray-300">
                {t('companyImport.name')}
              </label>
              <input id="company-import-name" className="input w-full" value={name} maxLength={200}
                onChange={e => setName(e.target.value)} />
            </div>
          )}
          {importing.isError && (
            <p className="text-xs text-red-400" role="alert">
              {t('companyImport.failed', { message: errorDetail(importing.error) })}
            </p>
          )}
          <div className="flex gap-2">
            <button type="button" className="btn-primary" disabled={!picked || importing.isPending} onClick={submit}>
              {importing.isPending ? t('companyImport.importing') : t('companyImport.submit')}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setOpen(false)}>
              {t('companyImport.cancel')}
            </button>
          </div>
        </>
      )}
    </section>
  )
}
