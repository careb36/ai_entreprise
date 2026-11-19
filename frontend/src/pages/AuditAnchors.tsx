import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Anchor, CircleAlert, CircleCheck, CircleDashed, Download, Hourglass, MessageSquareQuote, ShieldCheck, ShieldX, TriangleAlert,
  type LucideIcon,
} from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { errorDetail } from '../services/api'
import {
  useAnchorCoverage,
  useAnchorNow,
  useAnchorSettings,
  useSaveAnchorSettings,
  type AnchorCoverage,
  type AnchorSettings,
  type AnchorSettingsInput,
  type AnchorState,
  type CoverageStatus,
} from '../services/auditAnchors'
import { useExportAnchor } from '../services/auditTrail'
import { saveJson } from '../lib/saveJson'
import { useAppStore } from '../store/useAppStore'
import { AccessNotice, CountPill, FormActions, LoadError, LoadingRows, PageHeader, Panel, SavedNote, Spinner, StatusBadge, type StatusTone, TABLE, TD, TH, TR } from '../components/ui'

/** The coverage as an icon, a sentence and a tone: never the colour alone. */
const COVERAGE_LOOK: Record<CoverageStatus, { icon: LucideIcon; text: string }> = {
  empty: { icon: CircleDashed, text: 'text-gray-300' },
  no_anchors: { icon: TriangleAlert, text: 'text-amber-300' },
  uncovered_tail: { icon: TriangleAlert, text: 'text-amber-300' },
  covered: { icon: ShieldCheck, text: 'text-emerald-300' },
  anchor_mismatch: { icon: ShieldX, text: 'text-red-300' },
}

/** What was observed (verified, pending, undelivered, mismatch); a claim is drawn apart, dashed. */
const STATE_LOOK: Record<Exclude<AnchorState, 'claim'>, { icon: LucideIcon; tone: StatusTone }> = {
  verified: { icon: CircleCheck, tone: 'success' },
  pending: { icon: Hourglass, tone: 'neutral' },
  undelivered: { icon: TriangleAlert, tone: 'warning' },
  mismatch: { icon: ShieldX, tone: 'danger' },
}

const UNKNOWN_STATE: { icon: LucideIcon; tone: StatusTone } = { icon: CircleAlert, tone: 'neutral' }

/**
 * Audit anchors (issue #542), for Owners and Managers: how much of the
 * company's audit chain is anchored outside the factory, the tail that is
 * not, any anchor that no longer matches, and the settings (Owner only).
 */
export default function AuditAnchors() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const allowed = role === 'Owner' || role === 'Manager'
  const coverage = useAnchorCoverage(companyId, allowed)
  const settings = useAnchorSettings(companyId, allowed)
  const anchorNow = useAnchorNow(companyId)

  return (
    <div className="page-container max-w-4xl space-y-4">
      <PageHeader
        title={t('auditAnchors.title')}
        subtitle={t('auditAnchors.subtitle')}
        actions={allowed && (
          <>
            {anchorNow.isSuccess && <SavedNote>{t('auditAnchors.queued', { count: anchorNow.data.queued.length })}</SavedNote>}
            {anchorNow.isError && <span role="alert" className="text-xs text-red-300">{errorDetail(anchorNow.error)}</span>}
            <button type="button" className="btn-tool border border-surface-700" disabled={anchorNow.isPending} onClick={() => anchorNow.mutate()}>
              {anchorNow.isPending ? <Spinner size={13} /> : <Anchor size={13} aria-hidden="true" />}
              {t('auditAnchors.anchorNow')}
            </button>
          </>
        )}
      />
      {!allowed ? (
        <AccessNotice>{t('auditAnchors.managersOnly', { role: role ?? '—' })}</AccessNotice>
      ) : (
        <div className="space-y-4">
          {coverage.isLoading ? (
            <LoadingRows rows={1} />
          ) : coverage.isError ? (
            <LoadError onRetry={() => coverage.refetch()}>{errorDetail(coverage.error)}</LoadError>
          ) : coverage.data ? (
            <CoverageCard coverage={coverage.data} />
          ) : null}
          {settings.data && (
            <SettingsForm key={settings.data.updated_at ?? 'new'} initial={settings.data} editable={role === 'Owner'} />
          )}
          {settings.isError && <div role="alert" className="alert-error text-sm">{errorDetail(settings.error)}</div>}
        </div>
      )}
    </div>
  )
}

function AnchorStateBadge({ state }: { state: AnchorState }) {
  const { t } = useTranslation()
  const label = t(`auditAnchors.state.${state}`)
  if (state === 'claim') {
    // A claim is what the endpoint says it kept, not something observed: dashed, as on the board.
    return (
      <span className="status-badge border-dashed border-surface-600 text-gray-300">
        <MessageSquareQuote size={11} aria-hidden="true" className="flex-shrink-0" />{label}
      </span>
    )
  }
  const look = STATE_LOOK[state] ?? UNKNOWN_STATE
  return <StatusBadge icon={look.icon} tone={look.tone} label={label} />
}

function CoverageCard({ coverage }: { coverage: AnchorCoverage }) {
  const { t } = useTranslation()
  const look = COVERAGE_LOOK[coverage.status] ?? COVERAGE_LOOK.empty
  const Icon = look.icon
  return (
    <Panel title={t('auditAnchors.coverage')}
      count={coverage.anchors.length > 0
        ? <CountPill count={coverage.anchors.length} label={t('auditAnchors.anchorCount', { count: coverage.anchors.length })} />
        : undefined}>
      <div className="space-y-3 p-3">
        <p data-testid="coverage-status" className={`flex items-start gap-1.5 text-[13px] font-medium ${look.text}`}>
          <Icon size={14} aria-hidden="true" className="mt-0.5 flex-shrink-0" />
          {t(`auditAnchors.status.${coverage.status}`)}
        </p>
        <dl className="grid grid-cols-1 gap-px overflow-hidden rounded-md border border-surface-700 bg-surface-700 sm:grid-cols-3">
          {[
            { label: t('auditAnchors.head'), value: coverage.head_seq },
            { label: t('auditAnchors.verifiedThrough'), value: coverage.verified_through },
            { label: t('auditAnchors.coveredThrough'), value: coverage.covered_through },
          ].map(({ label, value }) => (
            <div key={label} className="bg-surface-900 px-3 py-2">
              <dt className="text-meta">{label}</dt>
              <dd className="mt-0.5 text-lg font-semibold tabular-nums text-gray-100">{value}</dd>
            </div>
          ))}
        </dl>
        {coverage.uncovered && (
          <p className="flex items-start gap-1.5 text-xs text-amber-300">
            <TriangleAlert size={13} aria-hidden="true" className="mt-px flex-shrink-0" />
            {t('auditAnchors.uncovered', { from: coverage.uncovered.from, to: coverage.uncovered.to })}
          </p>
        )}
        {coverage.failures.length > 0 && (
          <ul role="alert" className="alert-error list-none flex-col gap-1 text-sm">
            {coverage.failures.map(f => (
              <li key={f.anchor_id} className="flex items-start gap-1.5">
                <ShieldX size={14} aria-hidden="true" className="mt-0.5 flex-shrink-0" />
                {t('auditAnchors.failure', { kind: t(`auditAnchors.kind.${f.kind}`), seq: f.seq, reason: f.reason })}
              </li>
            ))}
          </ul>
        )}
      </div>
      {coverage.anchors.length > 0 && (
        <div className="overflow-x-auto">
          <table className={TABLE}>
            <thead>
              <tr>
                <th className={TH}>{t('auditAnchors.columns.kind')}</th>
                <th className={`${TH} text-right`}>{t('auditAnchors.columns.seq')}</th>
                <th className={TH}>{t('auditAnchors.columns.state')}</th>
                <th className={TH}>{t('auditAnchors.columns.anchoredAt')}</th>
                <th className={TH}><span className="sr-only">{t('auditAnchors.columns.receipt')}</span></th>
              </tr>
            </thead>
            <tbody>
              {coverage.anchors.map(a => (
                <tr key={a.id} className={TR}>
                  <td className={TD}>{t(`auditAnchors.kind.${a.kind}`)}</td>
                  <td className={`${TD} text-right tabular-nums`}>{a.seq}</td>
                  <td className={TD} title={a.problem ?? undefined}><AnchorStateBadge state={a.state} /></td>
                  <td className={`${TD} tabular-nums text-gray-400`}>{a.anchored_at}</td>
                  <td className={`${TD} text-right`}><ExportReceipt anchorId={a.id} seq={a.seq} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

/** The anchor's statement and proof as a JSON file, to verify outside the factory. */
function ExportReceipt({ anchorId, seq }: { anchorId: number; seq: number }) {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const exporter = useExportAnchor(companyId)
  return (
    <>
      <button type="button" className="btn-tool" disabled={exporter.isPending}
        aria-label={t('auditAnchors.exportNamed', { seq })}
        onClick={() => exporter.mutate(anchorId, { onSuccess: data => saveJson(`audit-anchor-${companyId}-${anchorId}.json`, data) })}>
        {exporter.isPending ? <Spinner size={13} /> : <Download size={13} aria-hidden="true" />}{t('auditAnchors.export')}
      </button>
      {exporter.isError && <span role="alert" className="block text-xs text-red-300">{errorDetail(exporter.error)}</span>}
    </>
  )
}

function SettingsForm({ initial, editable }: { initial: AnchorSettings; editable: boolean }) {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const save = useSaveAnchorSettings(companyId)
  const [form, setForm] = useState({
    enabled: initial.enabled,
    cadence_minutes: initial.cadence_minutes,
    publish_webhook: initial.publish_webhook,
    tsa_url: initial.tsa_url ?? '',
    tsa_cert_pem: '',
    add_public_key: '',
  })

  const submit = (extra: AnchorSettingsInput = {}) => {
    const data: AnchorSettingsInput = {
      enabled: form.enabled,
      cadence_minutes: form.cadence_minutes,
      publish_webhook: form.publish_webhook,
      tsa_url: form.tsa_url.trim(),
      ...extra,
    }
    // The certificate and a new key are sent only when typed: empty keeps what is stored.
    if (form.tsa_cert_pem.trim()) data.tsa_cert_pem = form.tsa_cert_pem
    if (form.add_public_key.trim()) data.add_public_key = form.add_public_key
    save.mutate(data)
  }

  const check = 'h-4 w-4 flex-shrink-0 rounded accent-brand-500'
  return (
    <Panel title={t('auditAnchors.settings')}>
      <form className="space-y-4 p-3" onSubmit={e => { e.preventDefault(); submit() }}>
        {!editable && <p role="status" className="text-xs text-gray-400">{t('auditAnchors.ownerEdits')}</p>}
        <fieldset disabled={!editable} className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <label className="flex items-center gap-2 text-[13px] text-gray-200">
                <input type="checkbox" className={check} checked={form.enabled} onChange={e => setForm({ ...form, enabled: e.target.checked })} />
                {t('auditAnchors.enabled')}
              </label>
              <label className="flex items-center gap-2 text-[13px] text-gray-200">
                <input type="checkbox" className={check} checked={form.publish_webhook} onChange={e => setForm({ ...form, publish_webhook: e.target.checked })} />
                {t('auditAnchors.publishWebhook')}
              </label>
              {!initial.webhook_configured && <p className="pl-6 text-xs text-gray-400">{t('auditAnchors.noWebhook')}</p>}
            </div>
            <label className="block">
              <span className="input-label">{t('auditAnchors.cadence')}</span>
              <input type="number" min={15} max={1440} className="input w-full tabular-nums" value={form.cadence_minutes}
                onChange={e => setForm({ ...form, cadence_minutes: Number(e.target.value) })} />
            </label>
          </div>
          <label className="block">
            <span className="input-label">{t('auditAnchors.tsaUrl')}</span>
            <input className="input w-full" value={form.tsa_url} onChange={e => setForm({ ...form, tsa_url: e.target.value })} />
          </label>
          <label className="block">
            <span className="input-label">{t('auditAnchors.tsaCert')}</span>
            <textarea className="textarea w-full font-mono text-xs" rows={3} value={form.tsa_cert_pem}
              placeholder={initial.tsa_cert_sha256 ? t('auditAnchors.tsaCertStored', { sha256: initial.tsa_cert_sha256 }) : ''}
              onChange={e => setForm({ ...form, tsa_cert_pem: e.target.value })} />
          </label>
          <div className="space-y-2 border-t border-surface-700 pt-3">
            <h3 className="text-[13px] font-semibold text-gray-100">{t('auditAnchors.customerKeys')}</h3>
            <p className="text-xs text-gray-400">{t('auditAnchors.customerKeysHelp')}</p>
            {initial.customer_keys.length > 0 && (
              <ul className="data-list rounded-md border border-surface-700 text-xs">
                {initial.customer_keys.map(key => (
                  <li key={key.fingerprint} className="flex items-center gap-2 px-3 py-2">
                    <code className="min-w-0 flex-1 break-all font-mono text-gray-200">{key.fingerprint}</code>
                    {key.retired_at ? (
                      <span className="text-meta">{t('auditAnchors.retired')}</span>
                    ) : (
                      <button type="button" className="btn-tool border border-surface-700" onClick={() => submit({ retire_fingerprint: key.fingerprint })}>
                        {t('auditAnchors.retire')}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <label className="block">
              <span className="input-label">{t('auditAnchors.addKey')}</span>
              <textarea className="textarea w-full font-mono text-xs" rows={3} value={form.add_public_key}
                onChange={e => setForm({ ...form, add_public_key: e.target.value })} />
            </label>
          </div>
          <FormActions>
            <button type="submit" className="btn-primary btn-sm" disabled={save.isPending}>
              {save.isPending && <Spinner size={13} />}
              {t('auditAnchors.save')}
            </button>
            {save.isSuccess && <SavedNote>{t('auditAnchors.saved')}</SavedNote>}
          </FormActions>
        </fieldset>
        {save.isError && <p role="alert" className="alert-error text-sm">{errorDetail(save.error)}</p>}
      </form>
    </Panel>
  )
}
