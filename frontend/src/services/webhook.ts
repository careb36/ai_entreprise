/**
 * The company's notification webhook (`routers/webhooks.py`): the URL, the
 * events it receives, whether it is on, how the last delivery went. The
 * signing secret is never returned (`has_secret` only). Every member reads
 * it; a Manager creates or replaces it (an omitted secret keeps the stored
 * one), deletes it and queues a test ping.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'

export interface CompanyWebhook {
  url: string
  events: string[]
  enabled: boolean
  has_secret: boolean
  last_delivery_at: string | null
  last_status: number | string | null
  last_error: string | null
}

export interface WebhookSetting {
  company_id: number
  /** Every event the backend can send. */
  events: string[]
  webhook: CompanyWebhook | null
}

export interface WebhookInput {
  url: string
  /** Omit to keep the stored secret; required for a new webhook, at least 16 characters. */
  secret?: string
  events: string[]
  enabled: boolean
}

export const MIN_SECRET = 16

const url = (companyId: number) => `/v1/companies/${companyId}/webhook`

export const webhookApi = {
  get: (companyId: number) => api.get<WebhookSetting>(url(companyId)),
  save: (companyId: number, input: WebhookInput) => api.put<{ company_id: number; webhook: CompanyWebhook }>(url(companyId), input),
  remove: (companyId: number) => api.delete<{ company_id: number; webhook: null }>(url(companyId)),
  ping: (companyId: number) => api.post<{ queued: string }>(`${url(companyId)}/ping`, {}),
}

const key = (companyId: number | null) => ['company-webhook', companyId] as const

export function useWebhook(companyId: number | null) {
  return useQuery({ queryKey: key(companyId), queryFn: () => webhookApi.get(companyId as number), enabled: companyId !== null })
}

type WebhookChange = { kind: 'save'; input: WebhookInput } | { kind: 'remove' }

export function useWebhookChange(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (change: WebhookChange): Promise<unknown> =>
      change.kind === 'save' ? webhookApi.save(companyId as number, change.input) : webhookApi.remove(companyId as number),
    onSuccess: () => client.invalidateQueries({ queryKey: key(companyId) }),
  })
}

export function usePingWebhook(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => webhookApi.ping(companyId as number),
    onSuccess: () => client.invalidateQueries({ queryKey: key(companyId) }),
  })
}
