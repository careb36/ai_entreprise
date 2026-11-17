import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleCheck, CircleDashed, CirclePower, CircleX, Loader2, Plug, Trash2 } from 'lucide-react'
import { errorDetail } from '../services/api'
import { modelsApi, OPENAI_COMPATIBLE, type NewConnection, type ProviderConnection } from '../services/models'
import { CountPill, EmptyState, Panel, StatusBadge } from './ui'

function Loader({ size }: { size: number }) {
  return <Loader2 size={size} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
}

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
    <Panel title={t('models.connections.title')}
      count={query.data && <CountPill count={connections.length} label={t('models.connections.count', { count: connections.length })} />}>
      <p className="px-3 pt-3 text-xs text-gray-400">{t('models.connections.subtitle')}</p>
      {error && <div className="px-3 pt-3"><div role="alert" className="alert-error text-sm">{error}</div></div>}
      {query.isLoading ? (
        <div role="status" className="flex items-center gap-2 px-3 py-3 text-xs text-gray-400">
          <Loader size={13} /> {t('common.loading')}
        </div>
      ) : query.isError ? (
        <div className="p-3"><div role="alert" className="alert-error text-sm">{errorDetail(query.error)}</div></div>
      ) : connections.length === 0 ? (
        <EmptyState icon={Plug} title={t('models.connections.none')} />
      ) : (
        <ul className="data-list mt-2 border-t border-surface-700">
          {connections.map(row => (
            <li key={row.id} className="data-row flex flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[13px] font-semibold text-gray-100">{row.name}</span>
                  <span className="chip">{row.provider}</span>
                  {!row.enabled && <StatusBadge icon={CirclePower} label={t('models.connections.off')} />}
                </div>
                <div className="text-meta mt-0.5 truncate font-mono">{row.base_url}</div>
                <ValidationState row={row} />
              </div>
              {canManage && (
                <div className="flex flex-wrap gap-1">
                  <button type="button" className="btn-tool border border-surface-700" disabled={validate.isPending}
                    onClick={() => validate.mutate(row.id)}>
                    {t('models.connections.validate')}
                  </button>
                  <button type="button" className="btn-tool" disabled={toggle.isPending} onClick={() => toggle.mutate(row)}>
                    {row.enabled ? t('models.connections.turnOff') : t('models.connections.turnOn')}
                  </button>
                  <button type="button" className="btn-tool hover:text-red-300" disabled={remove.isPending}
                    aria-label={t('models.connections.removeNamed', { name: row.name })}
                    title={t('models.connections.removeNamed', { name: row.name })}
                    onClick={() => { if (window.confirm(t('models.connections.confirmRemove', { name: row.name }))) remove.mutate(row.id) }}>
                    <Trash2 size={13} aria-hidden="true" />
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {canManage && <NewConnectionForm companyId={companyId} onAdded={onSuccess} />}
    </Panel>
  )
}

function ValidationState({ row }: { row: ProviderConnection }) {
  const { t } = useTranslation()
  if (row.validation_ok === true) {
    return (
      <div className="mt-1 flex items-center gap-1 text-xs text-emerald-300">
        <CircleCheck size={12} aria-hidden="true" /> {t('models.connections.validated', { count: row.listed_models.length })}
      </div>
    )
  }
  if (row.validation_ok === false) {
    return (
      <div className="mt-1 flex items-center gap-1 text-xs text-red-300">
        <CircleX size={12} aria-hidden="true" /> {t('models.connections.failed', { detail: row.validation_detail ?? '' })}
      </div>
    )
  }
  return (
    <div className="mt-1 flex items-center gap-1 text-xs text-gray-400">
      <CircleDashed size={12} aria-hidden="true" /> {t('models.connections.notValidated')}
    </div>
  )
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
    <form aria-labelledby="new-connection" className="space-y-3 border-t border-surface-700 p-3"
      onSubmit={event => { event.preventDefault(); if (form.provider.trim()) connect.mutate() }}>
      <h3 id="new-connection" className="text-[13px] font-semibold text-gray-100">{t('models.connections.add')}</h3>
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
