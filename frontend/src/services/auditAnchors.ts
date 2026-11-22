/**
 * Audit anchors (issue #542, docs/archive/design/AUDIT_ANCHORS.md): the head of the
 * company's audit chain recorded outside the factory (the company's webhook,
 * an RFC 3161 timestamping authority, a key the customer holds), and how much
 * of the chain those anchors cover. Viewers read the coverage; a Manager
 * anchors now and reads the settings; only an Owner changes them.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'

export type AnchorKind = 'webhook' | 'tsa' | 'customer_key'
export type AnchorState = 'verified' | 'claim' | 'pending' | 'undelivered' | 'mismatch'
export type CoverageStatus = 'empty' | 'no_anchors' | 'uncovered_tail' | 'covered' | 'anchor_mismatch'

export interface AnchorRow {
  id: number
  kind: AnchorKind
  seq: number
  chain_hash: string
  anchored_at: string
  reason: string
  status: string
  state: AnchorState
  problem: string | null
  external_ref: string | null
  gen_time: string | null
  tsa_cert_sha256: string | null
}

export interface AnchorCoverage {
  company_id: number
  status: CoverageStatus
  head_seq: number
  covered_through: number
  verified_through: number
  segments: { from: number; to: number; strength: 'verified' | 'claim' }[]
  uncovered: { from: number; to: number } | null
  failures: { anchor_id: number; kind: AnchorKind; seq: number; reason: string }[]
  anchors: AnchorRow[]
}

export interface CustomerKey {
  fingerprint: string
  public_key: string
  added_at: string
  retired_at: string | null
}

export interface AnchorSettings {
  company_id: number
  enabled: boolean
  cadence_minutes: number
  publish_webhook: boolean
  webhook_configured: boolean
  tsa_url: string | null
  tsa_cert_sha256: string | null
  customer_keys: CustomerKey[]
  updated_by: string | null
  updated_at: string | null
}

/** Only the fields given are changed. */
export interface AnchorSettingsInput {
  enabled?: boolean
  cadence_minutes?: number
  publish_webhook?: boolean
  tsa_url?: string
  tsa_cert_pem?: string
  add_public_key?: string
  retire_fingerprint?: string
}

export const auditAnchorsApi = {
  coverage: (companyId: number) => api.get<AnchorCoverage>('/v1/audit/anchors', { company_id: companyId }),
  settings: (companyId: number) => api.get<AnchorSettings>('/v1/audit/anchors/settings', { company_id: companyId }),
  save: (companyId: number, data: AnchorSettingsInput) =>
    api.put<AnchorSettings>(`/v1/audit/anchors/settings?company_id=${companyId}`, data),
  anchorNow: (companyId: number) => api.post<{ queued: unknown[] }>(`/v1/audit/anchors?company_id=${companyId}`),
}

const coverageKey = (companyId: number | null) => ['audit-anchors', companyId] as const
const settingsKey = (companyId: number | null) => ['audit-anchor-settings', companyId] as const

export function useAnchorCoverage(companyId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: coverageKey(companyId),
    queryFn: () => auditAnchorsApi.coverage(companyId as number),
    enabled: enabled && companyId !== null,
  })
}

export function useAnchorSettings(companyId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: settingsKey(companyId),
    queryFn: () => auditAnchorsApi.settings(companyId as number),
    enabled: enabled && companyId !== null,
  })
}

export function useSaveAnchorSettings(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (data: AnchorSettingsInput) => auditAnchorsApi.save(companyId as number, data),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: settingsKey(companyId) })
      client.invalidateQueries({ queryKey: coverageKey(companyId) })
    },
  })
}

export function useAnchorNow(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => auditAnchorsApi.anchorNow(companyId as number),
    onSuccess: () => client.invalidateQueries({ queryKey: coverageKey(companyId) }),
  })
}
