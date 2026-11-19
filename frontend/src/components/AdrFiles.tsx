import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Download, FileText } from 'lucide-react'
import { errorDetail } from '../services/api'
import { useAdrFiles, type AdrFile } from '../services/architectureFiles'
import { saveText } from '../lib/saveFile'
import { FormActions, Panel, Spinner } from './ui'

/** The file name an ADR saves under: the last part of its repository path. */
export const adrFileName = (file: AdrFile) => file.path.split('/').pop() || `adr-${file.number}.md`

/**
 * An architecture's decisions as ADR files (`GET /v1/architectures/{id}/adrs`,
 * Viewer), numbered from the number the repository's ADRs continue at, each
 * to save as Markdown under `docs/adr/`. Read on request; nothing is written
 * to the project from here.
 */
export function AdrFiles({ architectureId }: { architectureId: number }) {
  const { t } = useTranslation()
  const adrs = useAdrFiles(architectureId)
  const ids = { first: useId() }
  const [first, setFirst] = useState('1')
  const firstNumber = Number(first)
  const valid = Number.isInteger(firstNumber) && firstNumber >= 1 && firstNumber <= 9999
  const files = adrs.data?.files ?? []

  return (
    <Panel headingLevel={2} title={<span className="flex items-center gap-1.5"><FileText size={13} aria-hidden="true" className="text-gray-400" />{t('architectureFiles.adrTitle')}</span>}>
      <form className="space-y-2 p-3" aria-label={t('architectureFiles.adrTitle')} onSubmit={event => { event.preventDefault(); if (valid) adrs.mutate(firstNumber) }}>
        <p className="text-xs text-gray-400">{t('architectureFiles.adrHint')}</p>
        <FormActions>
          <label htmlFor={ids.first} className="text-xs text-gray-300">{t('architectureFiles.firstNumber')}</label>
          <input id={ids.first} inputMode="numeric" value={first} onChange={event => setFirst(event.target.value)} aria-invalid={!valid}
            className="input control-sm w-20 tabular-nums" />
          <button type="submit" className="btn-tool border border-surface-700" disabled={!valid || adrs.isPending}>
            {adrs.isPending && <Spinner size={13} />}{t('architectureFiles.prepare')}
          </button>
          {adrs.isError && <span role="alert" className="text-xs text-red-300">{errorDetail(adrs.error)}</span>}
        </FormActions>
      </form>
      {adrs.isSuccess && (files.length === 0 ? (
        <p className="border-t border-surface-700 px-3 py-2 text-xs text-gray-400">{t('architectureFiles.noDecisions')}</p>
      ) : (
        <ul className="data-list border-t border-surface-700" aria-label={t('architectureFiles.adrList')}>
          {files.map(file => (
            <li key={file.id} className="data-row flex flex-wrap items-center gap-2 px-3 py-2 text-xs">
              <code className="font-mono text-gray-200">{file.path}</code>
              <button type="button" className="btn-tool ml-auto" aria-label={t('architectureFiles.downloadNamed', { path: file.path })}
                onClick={() => saveText(adrFileName(file), file.markdown, 'text/markdown')}>
                <Download size={13} aria-hidden="true" />{t('architectureFiles.download')}
              </button>
            </li>
          ))}
        </ul>
      ))}
    </Panel>
  )
}
