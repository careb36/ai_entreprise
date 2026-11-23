import { useTranslation } from 'react-i18next'
import { Download } from 'lucide-react'
import { useCompanyExport } from '../hooks/useCompanyExport'
import { errorDetail, type CompanyExport } from '../services/api'
import { saveJson } from '../lib/saveJson'
import { Panel } from './ui'

/**
 * `acme-corp-export-2026-11-22.json`: the company's name as the export holds
 * it, and the export's day.
 */
export function exportFileName(data: CompanyExport): string {
  const company = data.content.company as { company_name?: unknown } | undefined
  const name = typeof company?.company_name === 'string' ? company.company_name : ''
  const slug = name
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  const day = data.manifest.exported_at.slice(0, 10)
  return `${slug || `company-${data.manifest.company_id}`}-export-${day}.json`
}

/**
 * The company's configuration as a file another La Fabrique can import
 * (issue #774), for the Owner. The backend leaves secrets out and names them,
 * replaces lines that look like secrets, and audits the export; people and
 * history are not in the file.
 */
export default function CompanyExportPanel({ companyId }: { companyId: number | null }) {
  const { t } = useTranslation()
  const exporting = useCompanyExport(companyId)
  const done = exporting.data

  const run = () => {
    exporting.mutate(undefined, {
      onSuccess: data => saveJson(exportFileName(data), data),
    })
  }

  return (
    <Panel title={<span className="flex items-center gap-1.5"><Download size={13} aria-hidden="true" className="text-gray-400" />{t('companyExport.title')}</span>}>
      <div className="space-y-3 p-4">
        <p className="text-xs text-gray-400">{t('companyExport.hint')}</p>
        <button type="button" className="btn-secondary flex items-center gap-1.5"
          disabled={companyId === null || exporting.isPending} onClick={run}>
          <Download size={14} aria-hidden="true" />
          {exporting.isPending ? t('companyExport.exporting') : t('companyExport.submit')}
        </button>
        {exporting.isError && (
          <p role="alert" className="text-xs text-red-300">
            {t('companyExport.failed', { message: errorDetail(exporting.error) })}
          </p>
        )}
        {done && (
          <div role="status" className="space-y-1 text-xs text-gray-300">
            <p>{t('companyExport.saved', { name: exportFileName(done) })}</p>
            <p>{t('companyExport.leftOut', { count: done.required_secrets.length })}</p>
            {done.redacted.length > 0 && (
              <p>{t('companyExport.redacted', { count: done.redacted.length })}</p>
            )}
          </div>
        )}
      </div>
    </Panel>
  )
}
