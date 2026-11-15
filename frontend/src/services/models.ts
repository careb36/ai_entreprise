import { api } from './api'

/** A company's connection to a model provider (`/v1/companies/{id}/providers`). The key is never returned. */
export interface ProviderConnection {
  id: number
  provider: string
  name: string
  base_url: string
  api_key_set: boolean
  header_names: string[]
  enabled: boolean
  validated_at: string | null
  validation_ok: boolean | null
  validation_detail: string | null
  listed_models: string[]
}

export interface CatalogProvider {
  id: string
  name: string
  base_url: string
  model_count: number
}

export interface ConnectionModel {
  id: string
  name?: string
  tool_call?: boolean
  reasoning?: boolean
  context?: number
  cost_input?: number
  in_catalog: boolean
  listed: boolean
}

export interface ConnectionModels {
  connection_id: number
  name: string
  provider: string
  validation_ok: boolean | null
  models: ConnectionModel[]
}

export type ModelSource = 'role' | 'company' | 'deployment'

/**
 * The routing policy by task difficulty (issue #319): a band (`low`, `medium`,
 * `high`, or a range of scores such as `1-2`) to a model id or a proxy tier.
 */
export interface ModelPolicy {
  bands: Record<string, string>
  label?: string
}

export interface ModelSettings {
  default_model: string | null
  role_models: Record<string, string>
  roles: string[]
  effective: Record<string, { model: string; source: ModelSource }>
  /** The company's own policy; null: the deployment's, else none. */
  model_policy?: ModelPolicy | null
  deployment_policy?: ModelPolicy | null
  policy_source?: 'company' | 'deployment' | null
  difficulty_bands?: string[]
}

/** How a model did on the company's tasks in a role (completed sprints). */
export interface ModelRecord { tasks: number; done: number; rework: number }

export interface ModelSuggestion {
  model: string
  name: string
  connection: string
  reason: string
  /** The company's own record with the suggested model; when present it decided the suggestion. */
  measured?: ModelRecord | null
}

export interface NewConnection {
  provider: string
  name?: string
  base_url?: string
  api_key?: string
}

/** The provider id for an endpoint that is not in the catalogue. */
export const OPENAI_COMPATIBLE = 'openai-compatible'

/** A connection's model as an agent's model: `conn:<id>:<model>`. */
export function connectionModelRef(connectionId: number, modelId: string) {
  return `conn:${connectionId}:${modelId}`
}

export interface ModelOption { value: string; label: string; connection: string }

/**
 * The models the company's enabled connections confirmed, as `conn:<id>:<model>`:
 * only a connection validated with success, and only what its provider listed.
 * The catalogue says what a model can do, never that the provider serves it.
 */
export function connectionModelOptions(connections: ConnectionModels[]): ModelOption[] {
  return connections
    .filter(conn => conn.validation_ok === true)
    .flatMap(conn => conn.models
      .filter(m => m.listed)
      .map(m => ({
        value: connectionModelRef(conn.connection_id, m.id),
        label: `${conn.name} · ${m.name ?? m.id}`,
        connection: conn.name,
      })))
}

const base = (companyId: number) => `/v1/companies/${companyId}`

export const modelsApi = {
  connections: (companyId: number) =>
    api.get<{ providers: ProviderConnection[] }>(`${base(companyId)}/providers`),
  connect: (companyId: number, body: NewConnection) =>
    api.post<ProviderConnection>(`${base(companyId)}/providers`, body),
  update: (companyId: number, id: number, body: Partial<NewConnection> & { enabled?: boolean }) =>
    api.patch<ProviderConnection>(`${base(companyId)}/providers/${id}`, body),
  remove: (companyId: number, id: number) =>
    api.delete<void>(`${base(companyId)}/providers/${id}`),
  validate: (companyId: number, id: number) =>
    api.post<{ provider: ProviderConnection }>(`${base(companyId)}/providers/${id}/validate`),
  catalog: () =>
    api.get<{ origin: string; providers: CatalogProvider[] }>('/v1/models/catalog'),
  companyModels: (companyId: number) =>
    api.get<{ origin: string; connections: ConnectionModels[] }>(`${base(companyId)}/models`),
  settings: (companyId: number) =>
    api.get<ModelSettings>(`${base(companyId)}/model_settings`),
  saveSettings: (companyId: number, body: { default_model: string | null; role_models: Record<string, string> }) =>
    api.put<ModelSettings>(`${base(companyId)}/model_settings`, body),
  /** The routing policy, with the stored model choices sent back unchanged (a PUT replaces them). */
  savePolicy: (companyId: number, current: Pick<ModelSettings, 'default_model' | 'role_models'>, policy: ModelPolicy | null) =>
    api.put<ModelSettings>(`${base(companyId)}/model_settings`, {
      default_model: current.default_model,
      role_models: current.role_models,
      model_policy: policy,
    }),
  suggestions: (companyId: number) =>
    api.get<{ suggestions: Record<string, ModelSuggestion | null>; candidates: number }>(
      `${base(companyId)}/model_suggestions`,
    ),
}
