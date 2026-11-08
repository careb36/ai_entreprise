import { api } from './api'

export interface ProviderConnection {
  id: number
  name: string
  provider: string
  base_url: string | null
  enabled: boolean
  validation_ok: boolean | null
  listed_models: string[]
}

export interface ConnectionModels {
  connection_id: number
  name: string
  provider: string
  validation_ok: boolean | null
  models: Array<{ id: string; in_catalog: boolean; listed: boolean; name?: string }>
}

export const providersApi = {
  list: (companyId: number) =>
    api.get<{ connections: ProviderConnection[] }>(`/v1/companies/${companyId}/providers`),
  models: (companyId: number) =>
    api.get<{ origin: string; connections: ConnectionModels[] }>(`/v1/companies/${companyId}/models`),
  suggestions: (companyId: number) =>
    api.get<{ suggestions: Record<string, string | null> }>(`/v1/companies/${companyId}/model_suggestions`),
}