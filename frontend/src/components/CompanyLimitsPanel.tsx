import React from 'react'
import { useTranslation } from 'react-i18next'
import { useCompanyLimits, useSetCompanyLimits } from '../hooks/useCompanyLimits'
import { errorDetail, type CompanyLimitKey, type CompanyLimits } from '../services/api'
import { Panel } from './ui'

const KEYS: CompanyLimitKey[] = ['tool_rounds', 'result_max_chars', 'max_output_tokens', 'max_reworks']

/** The fields as the form holds them: '' is "the deployment's value". */
type Draft = Record<CompanyLimitKey, string>

function draftOf(limits: CompanyLimits | null): Draft {
  return Object.fromEntries(KEYS.map(key => [key, limits?.[key]?.toString() ?? ''])) as Draft
}

function limitsOf(draft: Draft): CompanyLimits | null {
  const entries = KEYS.filter(key => draft[key].trim() !== '').map(key => [key, Number(draft[key])])
  return entries.length ? (Object.fromEntries(entries) as CompanyLimits) : null
}

/**
 * The company's limits on its agents' work (issue #766): the Developer's tool
 * rounds, how much of a tool's result it sees, the cap on its answer, and a
 * task's rework budget. An empty field keeps the deployment's value, shown
 * beside it; each stays within the range the backend holds. Any member reads
 * them; only an Owner saves them, each change audited.
 */
export default function CompanyLimitsPanel({ companyId, isOwner }: { companyId: number | null; isOwner: boolean }) {
  const { t } = useTranslation()
  const setting = useCompanyLimits(companyId)
  const save = useSetCompanyLimits(companyId)
  const [draft, setDraft] = React.useState<Draft | null>(null)

  if (setting.isError) return <div role="alert" className="alert-error text-sm">{errorDetail(setting.error)}</div>
  if (!setting.data) return null
  const state = setting.data
  const current = draft ?? draftOf(state.limits)

  return (
    <Panel title={t('companyLimits.title')}>
      <div className="space-y-3 p-3">
        <p className="text-xs text-gray-400">{t('companyLimits.hint')}</p>
        <fieldset className="grid gap-2 sm:grid-cols-2" disabled={!isOwner || save.isPending}>
          <legend className="sr-only">{t('companyLimits.title')}</legend>
          {KEYS.map(key => (
            <label key={key} className="space-y-1 text-xs text-gray-300">
              <span className="block font-medium text-gray-200">{t(`companyLimits.field.${key}`)}</span>
              <input type="number" inputMode="numeric" min={state.floors[key]} max={state.ceilings[key]}
                value={current[key]} placeholder={String(state.defaults[key])}
                aria-describedby={`limit-${key}-range`}
                onChange={e => setDraft({ ...current, [key]: e.target.value })}
                className="w-full rounded border border-surface-700 bg-surface-900 px-1.5 py-0.5 text-gray-200" />
              <span id={`limit-${key}-range`} className="block text-gray-400">
                {t('companyLimits.range', {
                  deployment: state.defaults[key],
                  floor: state.floors[key],
                  ceiling: state.ceilings[key],
                })}
              </span>
            </label>
          ))}
        </fieldset>
        {save.isError && <div role="alert" className="alert-error text-sm">{errorDetail(save.error)}</div>}
        {isOwner ? (
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-primary btn-sm" disabled={save.isPending || draft === null}
              onClick={() => save.mutate(limitsOf(current), { onSuccess: () => setDraft(null) })}>
              {t('companyLimits.save')}
            </button>
            <button type="button" className="btn-ghost btn-sm" disabled={save.isPending || state.limits === null}
              onClick={() => save.mutate(null, { onSuccess: () => setDraft(null) })}>
              {t('companyLimits.reset')}
            </button>
          </div>
        ) : (
          <p className="text-xs text-gray-400">{t('companyLimits.ownerOnly')}</p>
        )}
      </div>
    </Panel>
  )
}
