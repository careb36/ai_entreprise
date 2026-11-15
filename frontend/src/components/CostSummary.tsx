/**
 * Tokens and cost of a run, a task or a sprint (`services/cost.ts`).
 *
 * The cost is shown as currency only when the backend priced every reported
 * turn; otherwise "price unknown" with the reason. Estimated tokens are shown
 * apart and labelled as such: they are never part of the cost.
 */
import { useTranslation } from 'react-i18next'
import { Coins } from 'lucide-react'
import { formatCostCents, formatTokens, useRunCost, useTaskCost, type CostSummary as Cost, type PromptWeight } from '../services/cost'
import { PromptWeightBreakdown } from './PromptWeight'

function CostValue({ cost }: { cost: Cost }) {
  const { t, i18n } = useTranslation()
  const money = formatCostCents(cost.cost_cents, i18n.language)
  if (money !== null) return <span className="font-medium text-white tabular-nums">{money}</span>
  const reason = cost.reason ?? 'price_unknown'
  return (
    <span className="text-amber-300" title={cost.unpriced_models.join(', ') || undefined}>
      {t(`cost.reason.${reason}`, { defaultValue: t('cost.priceUnknown') })}
    </span>
  )
}

/** One line: the cost, the reported tokens in and out, and the estimate apart. */
export function CostLine({ cost }: { cost: Cost }) {
  const { t } = useTranslation()
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-gray-400" data-testid="cost-line">
      <Coins size={12} className="shrink-0" aria-hidden="true" />
      <CostValue cost={cost} />
      {cost.reported_turns > 0 && (
        <span className="tabular-nums">
          {cost.input_tokens + cost.output_tokens === cost.reported_tokens
            ? t('cost.tokensInOut', { input: formatTokens(cost.input_tokens), output: formatTokens(cost.output_tokens) })
            : t('cost.reportedTokens', { tokens: formatTokens(cost.reported_tokens) })}
        </span>
      )}
      {cost.estimated_tokens > 0 && (
        <span className="tabular-nums text-gray-500">
          {t('cost.estimatedTokens', { tokens: formatTokens(cost.estimated_tokens) })}
        </span>
      )}
    </p>
  )
}

/** The block on the run and task pages: the line, the models it priced and,
 * when the backend measured the prompts, where their tokens went (#443). */
export function CostCard({ cost, title, weight }: { cost: Cost; title: string; weight?: PromptWeight }) {
  const { t } = useTranslation()
  return (
    <section aria-label={title} className="card p-4 mb-4 space-y-1">
      <h2 className="text-xs text-gray-500 uppercase tracking-wider font-semibold">{title}</h2>
      <CostLine cost={cost} />
      {cost.models.length > 0 && (
        <p className="text-[11px] text-gray-500">
          {t('cost.models', { models: cost.models.join(', '), source: cost.price_source })}
        </p>
      )}
      {cost.estimated_tokens > 0 && (
        <p className="text-[11px] text-gray-500">{t('cost.estimatedHint')}</p>
      )}
      {weight && weight.calls > 0 && (
        <details className="pt-1">
          <summary className="cursor-pointer text-[11px] text-gray-400">{t('cost.weightTitle')}</summary>
          <div className="mt-1"><PromptWeightBreakdown weight={weight} /></div>
        </details>
      )}
    </section>
  )
}

export function RunCostCard({ companyId, runId }: { companyId: number; runId: number }) {
  const { t } = useTranslation()
  const { data } = useRunCost(companyId, runId)
  return data ? <CostCard cost={data} title={t('cost.runTitle')} weight={data.prompt_weight} /> : null
}

export function TaskCostCard({ taskId }: { taskId: number }) {
  const { t } = useTranslation()
  const { data } = useTaskCost(taskId)
  return data ? <CostCard cost={data} title={t('cost.taskTitle')} weight={data.prompt_weight} /> : null
}
