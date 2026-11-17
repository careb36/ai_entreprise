import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleCheck, Loader2, Sparkles } from 'lucide-react'
import { errorDetail } from '../services/api'
import {
  connectionModelOptions,
  modelsApi,
  type ModelOption,
  type ModelSettings,
  type ModelSuggestion,
} from '../services/models'
import { Panel } from './ui'
import { TABLE, TD, TH, TR } from './adminTable'

type Option = ModelOption

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

  const options = useMemo(() => connectionModelOptions(models.data?.connections ?? []), [models.data])
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
      <div role="status" className="panel flex items-center gap-2 px-3 py-3 text-xs text-gray-400">
        <Loader2 size={13} aria-hidden="true" className="animate-spin motion-reduce:animate-none" /> {t('common.loading')}
      </div>
    )
  }
  const data = settings.data as ModelSettings
  // Nothing changes while a save is on its way: its answer replaces the draft.
  const locked = !canManage || save.isPending
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
    <Panel title={t('models.choices.title')}
      actions={canManage && Object.values(advice).some(Boolean) && (
        <button type="button" className="btn-tool" disabled={locked} onClick={useSuggestions}>
          <Sparkles size={13} aria-hidden="true" /> {t('models.choices.useSuggestions')}
        </button>
      )}>
      <div className="space-y-3 p-3">
        <p className="text-xs text-gray-400">{t('models.choices.subtitle')}</p>
        {options.length === 0 && <p className="text-xs text-amber-300">{t('models.choices.noConnections')}</p>}
        <div className="max-w-md">
          <ModelSelect id="model-default" label={t('models.choices.default')} value={draft.default_model}
            emptyLabel={t('models.choices.deploymentDefault')} options={options} disabled={locked}
            onChange={value => change(null, value)} />
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className={TABLE}>
          <thead>
            <tr>
              <th className={TH}>{t('models.choices.role')}</th>
              <th className={TH}>{t('models.choices.model')}</th>
              <th className={TH}>{t('models.choices.runs')}</th>
            </tr>
          </thead>
          <tbody>
            {data.roles.map(role => {
              const suggestion = advice[role]
              const effective = data.effective[role]
              return (
                <tr key={role} className={`${TR} align-top`}>
                  <td className={`${TD} text-[13px] font-medium text-gray-100`}>{role}</td>
                  <td className={TD}>
                    <ModelSelect id={`model-role-${role}`} label={role} hideLabel value={draft.role_models[role] ?? ''}
                      emptyLabel={t('models.choices.companyDefault')} options={options} suggestion={suggestion}
                      disabled={locked} onChange={value => change(role, value)} />
                    {suggestion && (
                      <div className="mt-1 text-xs text-gray-400" title={suggestion.reason}>
                        {t('models.choices.suggested', { model: `${suggestion.connection} · ${suggestion.name}` })}
                      </div>
                    )}
                    {suggestion?.measured && (
                      <div className="text-xs text-gray-400">
                        {t('models.choices.measured', {
                          tasks: suggestion.measured.tasks,
                          done: suggestion.measured.done,
                          rework: (suggestion.measured.rework / suggestion.measured.tasks).toFixed(1),
                        })}
                      </div>
                    )}
                  </td>
                  <td className={`${TD} text-gray-400`}>
                    <div className="break-all font-mono text-gray-200">{effective?.model}</div>
                    {effective && <div className="text-meta mt-0.5">{t(`models.choices.source.${effective.source}`)}</div>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {canManage && (
        <div className="flex flex-wrap items-center gap-2 border-t border-surface-700 p-3">
          <button type="button" className="btn-primary btn-sm" disabled={save.isPending} onClick={() => save.mutate()}>
            {t('common.save')}
          </button>
          {saved && (
            <span role="status" className="inline-flex items-center gap-1 text-xs text-emerald-300">
              <CircleCheck size={13} aria-hidden="true" />{t('models.choices.saved')}
            </span>
          )}
          {save.isError && <span role="alert" className="text-xs text-red-300">{errorDetail(save.error)}</span>}
        </div>
      )}
    </Panel>
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
  const [query, setQuery] = useState('')
  const known = new Set(options.map(o => o.value))
  const filtered = query.trim()
    ? options.filter(o => o.label.toLowerCase().includes(query.toLowerCase()) || o.value.toLowerCase().includes(query.toLowerCase()))
    : options
  const others = filtered.filter(o => o.value !== suggestion?.model)
  return (
    <div className="block">
      <label htmlFor={id} className="block">
        <span className={hideLabel ? 'sr-only' : 'input-label'}>{label}</span>
        <select id={id} className="select control-sm w-full" value={value} disabled={disabled} onChange={e => onChange(e.target.value)}>
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
      <input type="search" className="input control-sm mt-1 w-full" placeholder={t('models.choices.search', { defaultValue: 'Filter models…' })}
        aria-label={t('models.choices.search', { defaultValue: 'Filter models…' })}
        value={query} onChange={e => setQuery(e.target.value)} disabled={disabled} />
    </div>
  )
}
