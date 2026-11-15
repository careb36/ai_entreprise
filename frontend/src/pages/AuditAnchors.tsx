import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Anchor, Loader2 } from 'lucide-react'
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
} from '../services/auditAnchors'
import { useAppStore } from '../store/useAppStore'

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
    <div className="page-container max-w-3xl">
      <div className="page-header mb-6">
        <div>
          <div className="flex items-center gap-2">
            <Anchor className="text-brand-400" size={22} />
            <h1 className="page-title">{t('auditAnchors.title')}</h1>
          </div>
          <p className="text-gray-400 mt-1">{t('auditAnchors.subtitle')}</p>
        </div>
      </div>
      {!allowed ? (
        <div role="status" className="card p-4 text-sm text-gray-300">
          {t('auditAnchors.managersOnly', { role: role ?? '—' })}
        </div>
      ) : (
        <div className="space-y-6">
          {coverage.isLoading ? (
            <Loader2 className="animate-spin text-brand-400" size={20} />
          ) : coverage.isError ? (
            <div role="alert" className="alert-error text-sm">{errorDetail(coverage.error)}</div>
          ) : coverage.data ? (
            <CoverageCard coverage={coverage.data} />
          ) : null}
          <div className="flex items-center gap-3">
            <button type="button" className="btn-secondary" disabled={anchorNow.isPending} onClick={() => anchorNow.mutate()}>
              {t('auditAnchors.anchorNow')}
            </button>
            {anchorNow.isSuccess && <span role="status" className="text-sm text-gray-300">{t('auditAnchors.queued', { count: anchorNow.data.queued.length })}</span>}
            {anchorNow.isError && <span role="alert" className="text-sm text-red-300">{errorDetail(anchorNow.error)}</span>}
          </div>
          {settings.data && (
            <SettingsForm key={settings.data.updated_at ?? 'new'} initial={settings.data} editable={role === 'Owner'} />
          )}
          {settings.isError && <div role="alert" className="alert-error text-sm">{errorDetail(settings.error)}</div>}
        </div>
      )}
    </div>
  )
}

function CoverageCard({ coverage }: { coverage: AnchorCoverage }) {
  const { t } = useTranslation()
  return (
    <section className="card p-4 space-y-3" aria-label={t('auditAnchors.coverage')}>
      <h2 className="font-semibold">{t('auditAnchors.coverage')}</h2>
      <p data-testid="coverage-status" className={coverage.status === 'anchor_mismatch' ? 'text-red-300' : 'text-gray-200'}>
        {t(`auditAnchors.status.${coverage.status}`)}
      </p>
      <dl className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-sm">
        <div><dt className="text-gray-400">{t('auditAnchors.head')}</dt><dd>{coverage.head_seq}</dd></div>
        <div><dt className="text-gray-400">{t('auditAnchors.verifiedThrough')}</dt><dd>{coverage.verified_through}</dd></div>
        <div><dt className="text-gray-400">{t('auditAnchors.coveredThrough')}</dt><dd>{coverage.covered_through}</dd></div>
      </dl>
      {coverage.uncovered && (
        <p className="text-sm text-amber-300">{t('auditAnchors.uncovered', { from: coverage.uncovered.from, to: coverage.uncovered.to })}</p>
      )}
      {coverage.failures.length > 0 && (
        <ul role="alert" className="text-sm text-red-300 list-disc pl-5">
          {coverage.failures.map(f => (
            <li key={f.anchor_id}>{t('auditAnchors.failure', { kind: t(`auditAnchors.kind.${f.kind}`), seq: f.seq, reason: f.reason })}</li>
          ))}
        </ul>
      )}
      {coverage.anchors.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-400">
                <th>{t('auditAnchors.columns.kind')}</th>
                <th>{t('auditAnchors.columns.seq')}</th>
                <th>{t('auditAnchors.columns.state')}</th>
                <th>{t('auditAnchors.columns.anchoredAt')}</th>
              </tr>
            </thead>
            <tbody>
              {coverage.anchors.map(a => (
                <tr key={a.id}>
                  <td>{t(`auditAnchors.kind.${a.kind}`)}</td>
                  <td>{a.seq}</td>
                  <td title={a.problem ?? undefined}>{t(`auditAnchors.state.${a.state}`)}</td>
                  <td>{a.anchored_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
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

  return (
    <form className="card p-4 space-y-4" onSubmit={e => { e.preventDefault(); submit() }}>
      <h2 className="font-semibold">{t('auditAnchors.settings')}</h2>
      {!editable && <p role="status" className="text-sm text-gray-400">{t('auditAnchors.ownerEdits')}</p>}
      <fieldset disabled={!editable} className="space-y-4">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.enabled} onChange={e => setForm({ ...form, enabled: e.target.checked })} />
          {t('auditAnchors.enabled')}
        </label>
        <label className="block text-sm">
          <span className="text-gray-300">{t('auditAnchors.cadence')}</span>
          <input type="number" min={15} max={1440} className="input mt-1 w-full" value={form.cadence_minutes}
            onChange={e => setForm({ ...form, cadence_minutes: Number(e.target.value) })} />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.publish_webhook} onChange={e => setForm({ ...form, publish_webhook: e.target.checked })} />
          {t('auditAnchors.publishWebhook')}
        </label>
        {!initial.webhook_configured && <p className="text-xs text-gray-400">{t('auditAnchors.noWebhook')}</p>}
        <label className="block text-sm">
          <span className="text-gray-300">{t('auditAnchors.tsaUrl')}</span>
          <input className="input mt-1 w-full" value={form.tsa_url} onChange={e => setForm({ ...form, tsa_url: e.target.value })} />
        </label>
        <label className="block text-sm">
          <span className="text-gray-300">{t('auditAnchors.tsaCert')}</span>
          <textarea className="input mt-1 w-full font-mono text-xs" rows={3} value={form.tsa_cert_pem}
            placeholder={initial.tsa_cert_sha256 ? t('auditAnchors.tsaCertStored', { sha256: initial.tsa_cert_sha256 }) : ''}
            onChange={e => setForm({ ...form, tsa_cert_pem: e.target.value })} />
        </label>
        <div className="space-y-2">
          <h3 className="text-sm text-gray-300">{t('auditAnchors.customerKeys')}</h3>
          <p className="text-xs text-gray-400">{t('auditAnchors.customerKeysHelp')}</p>
          <ul className="text-xs space-y-1">
            {initial.customer_keys.map(key => (
              <li key={key.fingerprint} className="flex items-center gap-2">
                <code className="break-all">{key.fingerprint}</code>
                {key.retired_at ? (
                  <span className="text-gray-400">{t('auditAnchors.retired')}</span>
                ) : (
                  <button type="button" className="btn-secondary text-xs" onClick={() => submit({ retire_fingerprint: key.fingerprint })}>
                    {t('auditAnchors.retire')}
                  </button>
                )}
              </li>
            ))}
          </ul>
          <label className="block text-sm">
            <span className="text-gray-300">{t('auditAnchors.addKey')}</span>
            <textarea className="input mt-1 w-full font-mono text-xs" rows={3} value={form.add_public_key}
              onChange={e => setForm({ ...form, add_public_key: e.target.value })} />
          </label>
        </div>
        <button type="submit" className="btn-primary" disabled={save.isPending}>{t('auditAnchors.save')}</button>
      </fieldset>
      {save.isSuccess && <p role="status" className="text-sm text-green-300">{t('auditAnchors.saved')}</p>}
      {save.isError && <p role="alert" className="text-sm text-red-300">{errorDetail(save.error)}</p>}
    </form>
  )
}
