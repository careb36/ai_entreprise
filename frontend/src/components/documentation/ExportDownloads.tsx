import { useTranslation } from 'react-i18next'
import { FileDown, FileCheck2 } from 'lucide-react'
import { errorDetail } from '../../services/api'
import { EXPORT_FORMATS, type ExportFormat, useDownloadExport, useExportFormats } from '../../services/packExport'
import { saveBlob } from '../../lib/saveFile'
import { Spinner } from '../ui'

/**
 * The pack as DOCX or PDF (PDF/A-3b, declaring PDF/UA-1), rendered by the
 * backend from the stored pack, and each export's signed manifest with the
 * file's sha256 (`GET /v1/documentation/{id}/export/{format}[/manifest]`,
 * Viewer). A format this deployment cannot render is shown disabled with the
 * backend's reason.
 */
export function ExportDownloads({ packId }: { packId: number }) {
  const { t } = useTranslation()
  const formats = useExportFormats()
  const download = useDownloadExport()
  const pending = download.isPending ? download.variables : undefined

  const save = (format: ExportFormat, manifest: boolean) =>
    download.mutate({ packId, format, manifest }, {
      onSuccess: ({ blob, filename }) => {
        const fallback = `documentation-pack-${packId}.${format}${manifest ? '.manifest.json' : ''}`
        saveBlob(filename ?? fallback, blob)
      },
    })

  return (
    <>
      {EXPORT_FORMATS.map(format => {
        const state = formats.data?.formats[format]
        const unavailable = state !== undefined && !state.available
        const reason = unavailable ? t('documentation.export.unavailable', { reason: state?.reason ?? '' }) : undefined
        const busy = (manifest: boolean) => pending?.format === format && Boolean(pending?.manifest) === manifest
        return (
          <span key={format} className="inline-flex items-center gap-1">
            <button type="button" className="btn-tool" disabled={download.isPending || unavailable} title={reason}
              onClick={() => save(format, false)}>
              {busy(false) ? <Spinner size={13} /> : <FileDown size={13} aria-hidden="true" />}{t(`documentation.export.${format}`)}
            </button>
            <button type="button" className="btn-tool" disabled={download.isPending || unavailable} title={reason ?? t('documentation.export.manifestHint')}
              aria-label={t('documentation.export.manifest', { format: format.toUpperCase() })}
              onClick={() => save(format, true)}>
              {busy(true) ? <Spinner size={13} /> : <FileCheck2 size={13} aria-hidden="true" />}
            </button>
          </span>
        )
      })}
      {download.isError && <span role="alert" className="text-xs text-red-300">{t('documentation.export.error', { message: errorDetail(download.error) })}</span>}
    </>
  )
}
