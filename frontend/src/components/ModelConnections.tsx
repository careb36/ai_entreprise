import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Loader2, Plug, Trash2, XCircle } from 'lucide-react'
import { errorDetail } from '../services/api'
import { modelsApi, OPENAI_COMPATIBLE, type NewConnection, type ProviderConnection } from '../services/models'

/** The query keys every part of the Models page refreshes after a change. */
export const MODEL_QUERY_KEYS = ['model-connections', 'company-models', 'model-suggestions', 'model-settings']

/**
 * A company's provider connections: which providers its agents can reach and
 * with which key. A Manager connects, validates (lists the provider's models
 * with the key, spending no tokens), turns off and removes them; a Viewer
 * reads them. The key never comes back from the server.
 */
export default function ModelConnections({ companyId, canManage }: { companyId: number; canManage: boolean }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const refresh = () => MODEL_QUERY_KEYS.forEach(key => queryClient.invalidateQueries({ queryKey: [key, companyId] }))
  const query = useQuery({
    queryKey: ['model-connections', companyId],
    queryFn: () => modelsApi.connections(companyId),
  })
  const [error, setError] = useState<string | null>(null)
  const onError = (err: unknown) => setError(errorDetail(err))
  const onSuccess = () => { setError(null); refresh() }
  const validate = useMutation({ mutationFn: (id: number) => modelsApi.validate(companyId, id), onSuccess, onError })
  const toggle = useMutation({
    mutationFn: (row: ProviderConnection) => modelsApi.update(companyId, row.id, { enabled: !row.enabled }),
    onSuccess, onError,
  })
  const remove = useMutation({ mutationFn: (id: number) => modelsApi.remove(companyId, id), onSuccess, onError })
  const connections = query.data?.providers ?? []

  return (
    <section aria-labelledby="model-connections" className="card space-y-4">
      <div className="flex items-center gap-2">
        <Plug size={18} className="text-brand-400" />
        <h2 id="model-connections" className="text-lg font-semibold">{t('models.connections.title')}</h2>
      </div>
      <p className="text-sm text-gray-400">{t('models.connections.subtitle')}</p>
      {error && <div role="alert" className="alert-error text-sm">{error}</div>}
      {query.isLoading ? (
        <div role="status" className="flex items-center gap-2 text-sm text-gray-400">
          <Loader2 size={15} className="animate-spin" /> {t('common.loading')}
        </div>
      ) : query.isError ? (
        <div role="alert" className="alert-error text-sm">{errorDetail(query.error)}</div>
      ) : connections.length === 0 ? (
        <p className="text-sm text-gray-400">{t('models.connections.none')}</p>
      ) : (
        <ul className="divide-y divide-surface-700">
          {connections.map(row => (
            <li key={row.id} className="flex flex-wrap items-center gap-3 py-3">
              <div className="min-w-0 flex-1">
                <div className="font-medium">
                  {row.name} <span className="text-xs text-gray-400">{row.provider}</span>
                  {!row.enabled && <span className="badge ml-2 bg-gray-800 text-gray-400">{t('models.connections.off')}</span>}
                </div>
                <div className="truncate text-xs text-gray-500">{row.base_url}</div>
                <ValidationState row={row} />
              </div>
              {canManage && (
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="btn-secondary btn-sm" disabled={validate.isPending}
                    onClick={() => validate.mutate(row.id)}>
                    {t('models.connections.validate')}
                  </button>
                  <button type="button" className="btn-ghost btn-sm" onClick={() => toggle.mutate(row)}>
                    {row.enabled ? t('models.connections.turnOff') : t('models.connections.turnOn')}
                  </button>
                  <button type="button" className="btn-danger btn-sm"
                    aria-label={t('models.connections.removeNamed', { name: row.name })}
                    onClick={() => { if (window.confirm(t('models.connections.confirmRemove', { name: row.name }))) remove.mutate(row.id) }}>
                    <Trash2 size={14} />
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {canManage && <NewConnectionForm companyId={companyId} onAdded={onSuccess} />}
    </section>
  )
}

function ValidationState({ row }: { row: ProviderConnection }) {
  const { t } = useTranslation()
  if (row.validation_ok === true) {
    return (
      <div className="mt-1 flex items-center gap-1 text-xs text-emerald-300">
        <CheckCircle2 size={13} /> {t('models.connections.validated', { count: row.listed_models.length })}
      </div>
    )
  }
  if (row.validation_ok === false) {
    return (
      <div className="mt-1 flex items-center gap-1 text-xs text-red-300">
        <XCircle size={13} /> {t('models.connections.failed', { detail: row.validation_detail ?? '' })}
      </div>
    )
  }
  return <div className="mt-1 text-xs text-gray-400">{t('models.connections.notValidated')}</div>
}

function NewConnectionForm({ companyId, onAdded }: { companyId: number; onAdded: () => void }) {
  const { t } = useTranslation()
  const catalog = useQuery({ queryKey: ['model-catalog'], queryFn: () => modelsApi.catalog(), staleTime: 3_600_000 })
  const [form, setForm] = useState<NewConnection>({ provider: '', name: '', base_url: '', api_key: '' })
  const [error, setError] = useState<string | null>(null)
  const providers = catalog.data?.providers ?? []
  const known = providers.find(p => p.id === form.provider)
  const custom = form.provider === OPENAI_COMPATIBLE
  const connect = useMutation({
    mutationFn: () => modelsApi.connect(companyId, {
      provider: form.provider.trim(),
      name: form.name?.trim() || undefined,
      base_url: form.base_url?.trim() || undefined,
      api_key: form.api_key || undefined,
    }),
    onSuccess: () => { setForm({ provider: '', name: '', base_url: '', api_key: '' }); setError(null); onAdded() },
    onError: (err: unknown) => setError(errorDetail(err)),
  })
  const set = (key: keyof NewConnection) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setForm(current => ({ ...current, [key]: event.target.value }))

  return (
    <form aria-labelledby="new-connection" className="space-y-3 border-t border-surface-700 pt-4"
      onSubmit={event => { event.preventDefault(); if (form.provider.trim()) connect.mutate() }}>
      <h3 id="new-connection" className="font-medium">{t('models.connections.add')}</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="input-label">{t('models.connections.provider')}</span>
          <input className="input w-full" list="model-providers" value={form.provider} onChange={set('provider')}
            placeholder={t('models.connections.providerPlaceholder')} required />
        </label>
        <datalist id="model-providers">
          <option value={OPENAI_COMPATIBLE}>{t('models.connections.openaiCompatible')}</option>
          {providers.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </datalist>
        <label className="block">
          <span className="input-label">{t('models.connections.name')}</span>
          <input className="input w-full" value={form.name} onChange={set('name')}
            placeholder={form.provider || t('models.connections.namePlaceholder')} />
        </label>
        <label className="block">
          <span className="input-label">{t('models.connections.baseUrl')}</span>
          <input className="input w-full" value={form.base_url} onChange={set('base_url')} required={custom}
            placeholder={known?.base_url ?? t('models.connections.baseUrlPlaceholder')} />
        </label>
        <label className="block">
          <span className="input-label">{t('models.connections.apiKey')}</span>
          <input className="input w-full" type="password" autoComplete="off" value={form.api_key} onChange={set('api_key')} />
        </label>
      </div>
      <p className="text-xs text-gray-400">{t('models.connections.keyNote')}</p>
      {error && <div role="alert" className="alert-error text-sm">{error}</div>}
      <button type="submit" className="btn-primary btn-sm" disabled={connect.isPending || !form.provider.trim()}>
        {t('models.connections.connect')}
      </button>
    </form>
  )
}
