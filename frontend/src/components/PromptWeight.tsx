/**
 * Where a prompt's tokens go: each section's estimated tokens and share, over
 * the prompts the task worker sent for a run, a task or a sprint (#443).
 *
 * The numbers are the backend's own measure of what it sent (characters / 4),
 * kept on each run's evidence; they are an estimate and never the provider's
 * count, which the cost line beside them reports.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Layers } from 'lucide-react'
import { formatTokens, useSprintCost, type PromptWeight } from '../services/cost'

export function PromptWeightBreakdown({ weight }: { weight: PromptWeight }) {
  const { t } = useTranslation()
  if (weight.calls === 0 || weight.sections.length === 0) {
    return <p className="text-[11px] text-gray-500">{t('promptWeight.none')}</p>
  }
  return (
    <div className="space-y-1" data-testid="prompt-weight">
      <p className="text-[11px] text-gray-500">
        {t('promptWeight.summary', {
          calls: weight.calls,
          tokens: formatTokens(weight.tokens),
          system: formatTokens(weight.system_tokens),
        })}
      </p>
      <ul className="space-y-1">
        {weight.sections.map(section => {
          const pct = Math.round(section.share * 100)
          return (
            <li key={section.name} className="grid grid-cols-[8rem_minmax(0,1fr)_6rem] items-center gap-2 text-[11px]">
              <span className="truncate text-gray-300" title={section.name}>
                {t(`promptWeight.section.${section.name}`, { defaultValue: section.name })}
              </span>
              <span className="h-1.5 overflow-hidden rounded-full bg-surface-700" aria-hidden="true">
                <span className="block h-full rounded-full bg-brand-500" style={{ width: `${pct}%` }} />
              </span>
              <span className="text-right tabular-nums text-gray-400">
                {t('promptWeight.tokensShare', { tokens: formatTokens(section.tokens), pct })}
                {section.cut > 0 && (
                  <span className="ml-1 text-amber-300" title={t('promptWeight.cutHint')}>
                    {t('promptWeight.cut', { count: section.cut })}
                  </span>
                )}
              </span>
            </li>
          )
        })}
      </ul>
      <p className="text-[11px] text-gray-500">{t('promptWeight.estimateHint')}</p>
    </div>
  )
}

/** A collapsed line on a sprint that loads the sprint's prompt weight when opened. */
export function SprintPromptWeight({ sprintId }: { sprintId: number }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const { data, isLoading, isError } = useSprintCost(sprintId, open)
  return (
    <div className="mt-1">
      <button
        type="button"
        className="flex items-center gap-1 text-[11px] text-gray-400 hover:text-brand-300"
        aria-expanded={open}
        onClick={() => setOpen(value => !value)}
      >
        <Layers size={11} aria-hidden="true" />
        {open ? t('promptWeight.hide') : t('promptWeight.show')}
      </button>
      {open && (
        <div className="mt-1">
          {isLoading && <p className="text-[11px] text-gray-500">{t('promptWeight.loading')}</p>}
          {isError && <p className="text-[11px] text-red-300">{t('promptWeight.error')}</p>}
          {data?.prompt_weight && <PromptWeightBreakdown weight={data.prompt_weight} />}
        </div>
      )}
    </div>
  )
}
