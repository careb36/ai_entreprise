import { useTranslation } from 'react-i18next'
import { Bot } from 'lucide-react'
import { useManagerRecord, type ManagerRecordCounts } from '../services/manager'

function formatRate(rate: number | null) {
  return rate === null ? '–' : `${Math.round(rate * 100)}%`
}

/**
 * The Manager agent's record beside the company's other numbers (issue #331):
 * per category, how often its suggestions matched the client's decisions and
 * how often the client overrode what it decided alone. Counted from the
 * database; nothing until the agent has looked at an item.
 */
export function ManagerRecordCard({ companyId }: { companyId: number }) {
  const { t } = useTranslation()
  const record = useManagerRecord(companyId)
  const data = record.data
  if (!data || data.total.suggested + data.total.taken_alone + data.total.decided === 0) return null
  const title = t('manager.recordTitle', { defaultValue: "The Manager agent's record" })
  const rows = Object.entries(data.categories).sort(([a], [b]) => a.localeCompare(b))
  const line = (c: ManagerRecordCounts) => [
    t('manager.recordAgreed', { agreed: c.agreed, decided: c.decided, defaultValue: 'matched your decision {{agreed}} of {{decided}}' }),
    t('manager.recordOverridden', { overridden: c.overridden, taken: c.taken_alone, rate: formatRate(c.override_rate), defaultValue: 'overridden {{overridden}} of {{taken}} taken alone ({{rate}})' }),
  ].join(' · ')

  return (
    <section aria-label={title} className="card space-y-2 p-4 text-sm">
      <h2 className="flex items-center gap-1.5 font-semibold text-white">
        <Bot size={14} aria-hidden="true" />
        {title}
      </h2>
      <p className="text-xs text-gray-500">
        {t('manager.recordHint', { defaultValue: 'Counted from what you decided and what you overrode; a rule whose overrides rise is flagged and no longer applied.' })}
      </p>
      <ul className="space-y-1">
        {rows.map(([category, counts]) => (
          <li key={category} className="text-xs text-gray-300">
            <span className="font-mono text-gray-200">{category}</span>
            {': '}
            {line(counts)}
          </li>
        ))}
      </ul>
      {data.flagged_rules.length > 0 && (
        <p className="text-xs text-amber-400">
          {t('manager.recordFlagged', { rules: data.flagged_rules.join(', '), defaultValue: 'Flagged, not applied: {{rules}}' })}
        </p>
      )}
    </section>
  )
}
