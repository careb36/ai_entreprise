import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cpu, Loader2, Sparkles } from 'lucide-react'
import { errorDetail } from '../services/api'
import {
  connectionModelRef,
  modelsApi,
  type ConnectionModels,
  type ModelSettings,
  type ModelSuggestion,
} from '../services/models'

interface Option { value: string; label: string }

/** Every model of the company's enabled connections, as `conn:<id>:<model>`. */
function modelOptions(connections: ConnectionModels[]): Option[] {
  return connections.flatMap(conn => {
    // A validated provider serves only what it listed; the catalogue alone is not enough.
    const listedOnly = conn.validation_ok === true && conn.models.some(m => m.listed)
    return conn.models
      .filter(m => !listedOnly || m.listed)
      .map(m => ({ value: connectionModelRef(conn.connection_id, m.id), label: `${conn.name} · ${m.name ?? m.id}` }))
  })
}

/**
 * The models the company's agents run: one default and one per role. An
 * agent's own model (the Agents page) still wins; without a choice here the
 * deployment's AGENT_MODEL settings apply. The suggestion beside each role is
 * a fixed rule over the catalogue, never applied until a Manager saves it.
 */
export default function ModelChoices({ companyId, canManage }: { companyId: number; canManage: boolean }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: ['model-settings', companyId], queryFn: () => modelsApi.settings(companyId) })
  const models = useQuery({ queryKey: ['company-models', companyId], queryFn: () => modelsApi.companyModels(companyId) })
  const suggestions = useQuery({ queryKey: ['model-suggestions', companyId], queryFn: () => modelsApi.suggestions(companyId) })
  const [draft, setDraft] = useState<{ default_model: string; role_models: Record<string, string> } | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (settings.data) {
      setDraft({ default_model: settings.data.default_model ?? '', role_models: { ...settings.data.role_models } })
    }
  }, [settings.data])

  const options = useMemo(() => modelOptions(models.data?.connections ?? []), [models.data])
  const save = useMutation({
    mutationFn: () => modelsApi.saveSettings(companyId, {
      default_model: draft?.default_model || null,
      role_models: Object.fromEntries(Object.entries(draft?.role_models ?? {}).filter(([, v]) => v)),
    }),
    onSuccess: data => {
      queryClient.setQueryData(['model-settings', companyId], data)
      setSaved(true)
    },
  })

  if (settings.isLoading || !draft) {
    return settings.isError ? (
      <div role="alert" className="alert-error text-sm">{errorDetail(settings.error)}</div>
    ) : (
      <div role="status" className="flex items-center gap-2 text-sm text-gray-400">
        <Loader2 size={15} className="animate-spin" /> {t('common.loading')}
      </div>
    )
  }
  const data = settings.data as ModelSettings
  const advice = suggestions.data?.suggestions ?? {}
  const change = (role: string | null, value: string) => {
    setSaved(false)
    setDraft(current => current && (role === null
      ? { ...current, default_model: value }
      : { ...current, role_models: { ...current.role_models, [role]: value } }))
  }
  const useSuggestions = () => {
    setSaved(false)
    setDraft(current => current && {
      ...current,
      role_models: {
        ...current.role_models,
        ...Object.fromEntries(Object.entries(advice).filter(([, s]) => s).map(([role, s]) => [role, (s as ModelSuggestion).model])),
      },
    })
  }

  return (
    <section aria-labelledby="model-choices" className="card space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Cpu size={18} className="text-brand-400" />
        <h2 id="model-choices" className="text-lg font-semibold">{t('models.choices.title')}</h2>
        {canManage && Object.values(advice).some(Boolean) && (
          <button type="button" className="btn-ghost btn-sm ml-auto" onClick={useSuggestions}>
            <Sparkles size={14} /> {t('models.choices.useSuggestions')}
          </button>
        )}
      </div>
      <p className="text-sm text-gray-400">{t('models.choices.subtitle')}</p>
      {options.length === 0 && <p className="text-sm text-amber-300">{t('models.choices.noConnections')}</p>}

      <ModelSelect id="model-default" label={t('models.choices.default')} value={draft.default_model}
        emptyLabel={t('models.choices.deploymentDefault')} options={options} disabled={!canManage}
        onChange={value => change(null, value)} />

      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-gray-400">
            <th className="py-1 pr-2 font-medium">{t('models.choices.role')}</th>
            <th className="py-1 pr-2 font-medium">{t('models.choices.model')}</th>
            <th className="py-1 font-medium">{t('models.choices.runs')}</th>
          </tr>
        </thead>
        <tbody>
          {data.roles.map(role => {
            const suggestion = advice[role]
            const effective = data.effective[role]
            return (
              <tr key={role} className="border-t border-surface-700 align-top">
                <td className="py-2 pr-2 font-medium">{role}</td>
                <td className="py-2 pr-2">
                  <ModelSelect id={`model-role-${role}`} label={role} hideLabel value={draft.role_models[role] ?? ''}
                    emptyLabel={t('models.choices.companyDefault')} options={options} suggestion={suggestion}
                    disabled={!canManage} onChange={value => change(role, value)} />
                  {suggestion && (
                    <div className="mt-1 text-xs text-gray-400" title={suggestion.reason}>
                      {t('models.choices.suggested', { model: `${suggestion.connection} · ${suggestion.name}` })}
                    </div>
                  )}
                </td>
                <td className="py-2 text-xs text-gray-400">
                  <div className="break-all text-gray-300">{effective?.model}</div>
                  {effective && <div>{t(`models.choices.source.${effective.source}`)}</div>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {canManage && (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className="btn-primary btn-sm" disabled={save.isPending} onClick={() => save.mutate()}>
            {t('common.save')}
          </button>
          {saved && <span role="status" className="text-sm text-emerald-300">{t('models.choices.saved')}</span>}
          {save.isError && <span role="alert" className="text-sm text-red-300">{errorDetail(save.error)}</span>}
        </div>
      )}
    </section>
  )
}

function ModelSelect({ id, label, hideLabel, value, emptyLabel, options, suggestion, disabled, onChange }: {
  id: string
  label: string
  hideLabel?: boolean
  value: string
  emptyLabel: string
  options: Option[]
  suggestion?: ModelSuggestion | null
  disabled: boolean
  onChange: (value: string) => void
}) {
  const { t } = useTranslation()
  const known = new Set(options.map(o => o.value))
  const others = options.filter(o => o.value !== suggestion?.model)
  return (
    <label htmlFor={id} className="block">
      <span className={hideLabel ? 'sr-only' : 'input-label'}>{label}</span>
      <select id={id} className="input w-full" value={value} disabled={disabled} onChange={e => onChange(e.target.value)}>
        <option value="">{emptyLabel}</option>
        {value && !known.has(value) && (
          <optgroup label={t('models.choices.current')}><option value={value}>{value}</option></optgroup>
        )}
        {suggestion && known.has(suggestion.model) && (
          <optgroup label={t('models.choices.suggestedGroup')}>
            <option value={suggestion.model}>{`${suggestion.connection} · ${suggestion.name}`}</option>
          </optgroup>
        )}
        {others.length > 0 && (
          <optgroup label={t('models.choices.others')}>
            {others.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </optgroup>
        )}
      </select>
    </label>
  )
}
