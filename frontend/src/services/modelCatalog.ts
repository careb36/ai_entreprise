/**
 * The public model catalogue (`routers/providers.py`, from models.dev): the
 * providers it knows and, for one provider, its models and what each can do.
 * It holds no company data. What a model can do is the catalogue's record,
 * not proof that a provider serves it to this company: only a validated
 * connection's listing says that (the Models page).
 */
import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import type { CatalogProvider } from './models'

export interface CatalogModel {
  id: string
  name?: string
  family?: string
  tool_call?: boolean
  reasoning?: boolean
  structured_output?: boolean
  attachment?: boolean
  open_weights?: boolean
  release_date?: string
  /** Tokens. */
  context?: number
  output?: number
  /** USD per million tokens, as the catalogue records it. */
  cost_input?: number
  cost_output?: number
}

export interface CatalogEntry {
  provider: string
  name: string
  base_url: string | null
  models: CatalogModel[]
}

export const modelCatalogApi = {
  providers: () => api.get<{ origin: string; providers: (CatalogProvider & { doc?: string | null })[] }>('/v1/models/catalog'),
  provider: (providerId: string) => api.get<CatalogEntry>(`/v1/models/catalog/${encodeURIComponent(providerId)}`),
}

export function useCatalogProviders() {
  return useQuery({ queryKey: ['model-catalog'], queryFn: modelCatalogApi.providers, staleTime: 5 * 60_000 })
}

export function useCatalogEntry(providerId: string | null) {
  return useQuery({
    queryKey: ['model-catalog', providerId],
    queryFn: () => modelCatalogApi.provider(providerId as string),
    enabled: providerId !== null,
    staleTime: 5 * 60_000,
  })
}
