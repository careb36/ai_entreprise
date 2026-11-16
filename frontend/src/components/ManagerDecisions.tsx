import { useTranslation } from 'react-i18next'
import { Bot, Undo2 } from 'lucide-react'
import { errorDetail } from '../services/api'
import { useClientBrief, useOverrideManagerDecision, type ManagerDecision } from '../services/manager'

/** Why the policy did not let the agent decide alone: the platform's sentence, apart from the claim. */
function RefusalReason({ reason }: { reason?: string | null }) {
  const { t } = useTranslation()
  if (!reason) return null
  return <p className="text-xs text-gray-500">{t('manager.refusal', { reason, defaultValue: 'Not decided alone: {{reason}}' })}</p>
}

/** The Manager agent's suggestion on an item: a claim, shown beside the client's choice. */
export function ManagerSuggestion({ suggestion }: { suggestion?: ManagerDecision | null }) {
  const { t } = useTranslation()
  if (!suggestion || suggestion.decided_by) return null
  return (
    <div className="space-y-0.5">
      <p className="flex items-center gap-1.5 text-xs text-gray-400" title={suggestion.suggested_reason ?? undefined}>
        <Bot size={12} aria-hidden="true" />
        {suggestion.suggested_decision
          ? t('manager.suggestion', { decision: suggestion.suggested_decision, defaultValue: 'Manager agent suggests: {{decision}}' })
          : t('manager.suggestionUnusable', { defaultValue: 'The Manager agent could not suggest; it is yours to decide.' })}
      </p>
      <RefusalReason reason={suggestion.refusal_reason} />
    </div>
  )
}

/** A short brief for the client, read from the board and the audit log. */
export function ClientBriefCard({ companyId }: { companyId: number }) {
  const { t } = useTranslation()
  const brief = useClientBrief(companyId)
  if (!brief.data) return null
  const { done, agent, waiting, cost } = brief.data
  return (
    <section aria-label={t('manager.briefTitle', { defaultValue: 'Your brief' })} className="card p-4 text-sm text-gray-300">
      <h2 className="mb-1 font-semibold text-gray-200">{t('manager.briefTitle', { defaultValue: 'Your brief' })}</h2>
      <p>
        {t('manager.briefLine', {
          done: done.count,
          decided: agent.taken_alone,
          overridden: agent.overridden,
          waiting: waiting.total,
          tokens: cost.tokens_since,
          defaultValue: 'Last day: {{done}} tasks done, {{decided}} decided by the Manager agent ({{overridden}} overridden), {{waiting}} waiting for you, {{tokens}} tokens.',
        })}
      </p>
      {cost.over_budget && (
        <p className="text-xs text-amber-400">{t('manager.overBudget', { defaultValue: 'Over the token budget: the agent decides nothing alone.' })}</p>
      )}
      {agent.refusals && agent.refusals.length > 0 && (
        <div className="mt-2">
          <h3 className="text-xs font-semibold text-gray-300">
            {t('manager.refusalsTitle', { count: agent.refused ?? agent.refusals.length, defaultValue: 'Not decided alone ({{count}})' })}
          </h3>
          <ul className="list-inside list-disc text-xs text-gray-400">
            {agent.refusals.map(r => (
              <li key={r.id}>
                {t('manager.refusalItem', {
                  decision: r.decision ?? t('manager.noDecision', { defaultValue: 'no usable suggestion' }),
                  kind: r.item_kind,
                  id: r.item_id,
                  reason: r.refusal_reason,
                  defaultValue: '{{decision}} ({{kind}} #{{id}}): {{reason}}',
                })}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

function Decision({ decision, canManage }: { decision: ManagerDecision; canManage: boolean }) {
  const { t } = useTranslation()
  const override = useOverrideManagerDecision()
  return (
    <article className="card flex flex-col gap-2 p-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 space-y-1 text-sm">
        <p className="font-semibold text-gray-200">
          {t('manager.decided', {
            decision: decision.suggested_decision,
            kind: decision.item_kind,
            id: decision.item_id,
            defaultValue: 'Decided by the Manager agent: {{decision}} ({{kind}} #{{id}})',
          })}
        </p>
        <p className="text-xs text-gray-400">{t('manager.rule', { rule: decision.rule, defaultValue: 'Allowed by the rule {{rule}}' })}</p>
        {decision.suggested_reason && <p className="text-xs text-gray-400">{decision.suggested_reason}</p>}
        {decision.overridden_by && (
          <p className="text-xs text-amber-400">{t('manager.overriddenBy', { who: decision.overridden_by, defaultValue: 'Overridden by {{who}}' })}</p>
        )}
        {override.isError && (
          <p role="alert" className="text-xs text-red-400">
            {t('manager.overrideError', { message: errorDetail(override.error), defaultValue: 'Could not override: {{message}}' })}
          </p>
        )}
      </div>
      {canManage && !decision.overridden_by && (
        <button className="btn-ghost btn-sm" disabled={override.isPending} onClick={() => override.mutate(decision.id)}>
          <Undo2 size={14} aria-hidden="true" />
          {t('manager.override', { defaultValue: 'Override' })}
        </button>
      )}
    </article>
  )
}

/** What the Manager agent decided alone under the company's policy, each with an override. */
export function ManagerDecisions({ decisions, canManage }: { decisions: ManagerDecision[]; canManage: boolean }) {
  const { t } = useTranslation()
  if (decisions.length === 0) return null
  return (
    <section aria-labelledby="manager-decisions" className="space-y-3">
      <h2 id="manager-decisions" className="text-base font-semibold text-white">
        {t('manager.decisionsTitle', { defaultValue: 'Decided by the Manager agent' })}
      </h2>
      {decisions.map(d => <Decision key={d.id} decision={d} canManage={canManage} />)}
    </section>
  )
}
