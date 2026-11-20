/**
 * A documentation pack exported as DOCX or PDF (issue #694, phase 4):
 * `GET /v1/documentation/{id}/export/{format}` (Viewer) renders the stored
 * pack and sends it as an attachment; `.../manifest` sends the export's signed
 * manifest, which lists the file's sha256 beside the pack's Markdown.
 * `GET /v1/documentation/exports` says which formats this deployment can
 * render (WeasyPrint needs native libraries) and why one cannot.
 */
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from './api'
import { fetchBlob } from './api/client'

export const EXPORT_FORMATS = ['docx', 'pdf'] as const
export type ExportFormat = typeof EXPORT_FORMATS[number]

export interface ExportFormats {
  formats: Record<ExportFormat, { available: boolean; reason: string | null }>
}

export const packExportApi = {
  formats: () => api.get<ExportFormats>('/v1/documentation/exports'),
  download: (packId: number, format: ExportFormat) => fetchBlob(`/v1/documentation/${packId}/export/${format}`),
  manifest: (packId: number, format: ExportFormat) => fetchBlob(`/v1/documentation/${packId}/export/${format}/manifest`),
}

export function useExportFormats() {
  return useQuery({ queryKey: ['documentation-export-formats'], queryFn: packExportApi.formats, staleTime: 5 * 60_000 })
}

export function useDownloadExport() {
  return useMutation({
    mutationFn: ({ packId, format, manifest }: { packId: number; format: ExportFormat; manifest?: boolean }) =>
      manifest ? packExportApi.manifest(packId, format) : packExportApi.download(packId, format),
  })
}
