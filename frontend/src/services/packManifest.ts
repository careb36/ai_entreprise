/**
 * A documentation pack's signed manifest (`GET /v1/documentation/{id}/manifest`,
 * Viewer; issue #694): every file with the sha256 the backend computed, the
 * signature and the audit chain's record of it, sent as an attachment.
 */
import { useMutation } from '@tanstack/react-query'
import { fetchBlob } from './api/client'

export const packManifestApi = {
  download: (packId: number) => fetchBlob(`/v1/documentation/${packId}/manifest`),
}

export function useDownloadManifest() {
  return useMutation({ mutationFn: (packId: number) => packManifestApi.download(packId) })
}
