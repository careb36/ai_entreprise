import { fetchApi } from './client'

export interface MemoryResult {
  content: string
  metadata: unknown
  similarity: number
}

export interface LineageEntry {
  engram_id: string
  role: string | null
  decision: string | null
  timestamp: string | null
  company_id: number | null
  legacy: boolean
}

export interface LineageResponse {
  file: string
  status: string
  recorded?: boolean; reason?: string // recorded: false when the graph is not kept, with the reason
  lineage: LineageEntry[]
}

export const memoryEndpoints = () => ({
  // Memory on pgvector (the graph is not needed). A 503 means no vector store.
  searchMemory: (query: string, options: { limit?: number; companyId?: number | null } = {}) =>
    fetchApi<{ results: MemoryResult[] }>('/v1/memory/search', {
      method: 'GET',
      params: {
        query,
        ...(options.limit ? { limit: options.limit } : {}),
        ...(options.companyId != null ? { company_id: options.companyId } : {}),
      },
      retryServerErrors: false,
    }),

  /** Stores *content* with its embedding (Manager). The API takes the text as a query parameter. */
  storeMemory: (content: string, options: { companyId?: number | null; metadata?: Record<string, unknown> } = {}) =>
    fetchApi<{ status: string }>('/v1/memory/store', {
      method: 'POST',
      params: {
        content,
        ...(options.companyId != null ? { company_id: options.companyId } : {}),
      },
      body: JSON.stringify(options.metadata ?? {}),
      retryServerErrors: false,
    }),

  getLineage: (filePath: string, companyId?: number | null) =>
    fetchApi<LineageResponse>('/v1/enterprise/lineage', {
      method: 'GET',
      params: {
        file_path: filePath,
        ...(companyId != null ? { company_id: companyId } : {}),
      },
      retryServerErrors: false,
    }),
})
