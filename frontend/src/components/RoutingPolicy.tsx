import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Gauge, Loader2 } from 'lucide-react'
import { errorDetail } from '../services/api'
import { modelsApi, type ModelPolicy, type ModelSettings } from '../services/models'

const BANDS = ['low', 'medium', 'high'] as const
type Draft = { bands: Record<string, string>; label: string }

function draftOf(policy: ModelPolicy | null | undefined): Draft {
  return { bands: { ...(policy?.bands ?? {}) }, label: policy?.label ?? '' }
}

/**
 * The company's routing policy by task difficulty (issue #319): a model id
 * (or a proxy tier such as `fast`) per difficulty band. The band is a fixed
 * estimate of the task's plan, never a model's opinion. A model a person chose
 * (the agent's, the company's default or per role) is never re-routed; without
 * a policy here the deployment's applies, else the role defaults. Whether it
 * pays is read on the Sprints page, by band and by served model.
 */
export default function RoutingPolicy({ companyId, canManage }: { companyId: number; canManage: boolean }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: ['model-settings', companyId], queryFn: () => modelsApi.settings(companyId) })
  const [draft, setDraft] = useState<Draft | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (settings.data) setDraft(draftOf(settings.data.model_policy))
  }, [settings.data])

  const save = useMutation({
    mutationFn: (policy: ModelPolicy | null) => modelsApi.savePolicy(companyId, settings.data as ModelSettings, policy),
    onSuccess: data => {
      queryClient.setQueryData(['model-settings', companyId], data)
      setSaved(true)
    },
  })

  if (!settings.data || !draft) {
    return settings.isError ? null : (
      <div role="status" className="flex items-center gap-2 text-sm text-gray-400">
        <Loader2 size={15} className="animate-spin" /> {t('common.loading')}
      </div>
    )
  }
  const data = settings.data
  const locked = !canManage || save.isPending
  const deployed = data.deployment_policy?.bands ?? {}
  const bands = Object.fromEntries(Object.entries(draft.bands).map(([k, v]) => [k, v.trim()]).filter(([, v]) => v))
  const policy: ModelPolicy | null = Object.keys(bands).length
    ? { bands, ...(draft.label.trim() ? { label: draft.label.trim() } : {}) }
    : null
  const edit = (next: Partial<Draft>) => {
    setSaved(false)
    setDraft(current => current && { ...current, ...next })
  }

  return (
    <section aria-labelledby="routing-policy" className="card space-y-3">
      <div className="flex items-center gap-2">
        <Gauge size={18} className="text-brand-400" />
        <h2 id="routing-policy" className="text-lg font-semibold">{t('models.policy.title')}</h2>
      </div>
      <p className="text-sm text-gray-400">{t('models.policy.subtitle')}</p>
      <p className="text-xs text-gray-500">
        {data.policy_source === 'company'
          ? t('models.policy.sourceCompany')
          : data.policy_source === 'deployment'
            ? t('models.policy.sourceDeployment')
            : t('models.policy.sourceNone')}
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        {BANDS.map(band => (
          <label key={band} htmlFor={`policy-${band}`} className="block">
            <span className="input-label">{t(`models.policy.band.${band}`)}</span>
            <input id={`policy-${band}`} className="input w-full" value={draft.bands[band] ?? ''} disabled={locked}
              placeholder={deployed[band] ?? t('models.policy.roleDefault')}
              onChange={e => edit({ bands: { ...draft.bands, [band]: e.target.value } })} />
          </label>
        ))}
      </div>
      <label htmlFor="policy-label" className="block max-w-xs">
        <span className="input-label">{t('models.policy.label')}</span>
        <input id="policy-label" className="input w-full" value={draft.label} disabled={locked}
          onChange={e => edit({ label: e.target.value })} />
      </label>
      <p className="text-xs text-gray-500">{t('models.policy.tiers')}</p>
      {canManage && (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className="btn-primary btn-sm" disabled={save.isPending} onClick={() => save.mutate(policy)}>
            {t('models.policy.save')}
          </button>
          {data.model_policy && (
            <button type="button" className="btn-ghost btn-sm" disabled={save.isPending}
              onClick={() => { setDraft(draftOf(null)); save.mutate(null) }}>
              {t('models.policy.clear')}
            </button>
          )}
          {saved && <span role="status" className="text-sm text-emerald-300">{t('models.policy.saved')}</span>}
          {save.isError && <span role="alert" className="text-sm text-red-300">{errorDetail(save.error)}</span>}
        </div>
      )}
    </section>
  )
}
