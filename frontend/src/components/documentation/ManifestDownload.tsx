import { useTranslation } from 'react-i18next'
import { FileDown } from 'lucide-react'
import { errorDetail } from '../../services/api'
import { useDownloadManifest } from '../../services/packManifest'
import { saveBlob } from '../../lib/saveFile'
import { Spinner } from '../ui'

/**
 * A pack's signed manifest, saved as the backend names it
 * (`GET /v1/documentation/{id}/manifest`, Viewer): every file's sha256, the
 * signature and the audit chain's record, to check the pack outside the factory.
 */
export function ManifestDownload({ packId }: { packId: number }) {
  const { t } = useTranslation()
  const download = useDownloadManifest()
  return (
    <>
      <button type="button" className="btn-tool" disabled={download.isPending}
        onClick={() => download.mutate(packId, { onSuccess: ({ blob, filename }) => saveBlob(filename ?? `documentation-pack-${packId}-manifest.json`, blob) })}>
        {download.isPending ? <Spinner size={13} /> : <FileDown size={13} aria-hidden="true" />}{t('documentation.manifest')}
      </button>
      {download.isError && <span role="alert" className="text-xs text-red-300">{t('documentation.manifestError', { message: errorDetail(download.error) })}</span>}
    </>
  )
}
