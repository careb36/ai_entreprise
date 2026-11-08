/**
 * The deterministic check's findings, as the specification and architecture
 * pages show them: a `Where` chip naming the element a finding is about
 * (`REQ-1.1`, `MOD-2`, `Q1`, …) and the blocking findings listed before the
 * warnings.
 */
import { useTranslation } from 'react-i18next'
import { CircleAlert } from 'lucide-react'
import type { Finding } from '../services/specifications'

export function Where({ where }: { where: string }) {
  return <code className="rounded bg-surface-800 px-1.5 py-0.5 font-mono text-[11px] text-gray-300">{where}</code>
}

export function FindingList({ findings, severity }: { findings: Finding[]; severity: Finding['severity'] }) {
  const { t } = useTranslation()
  const own = findings.filter(f => f.severity === severity)
  if (own.length === 0) return null
  const blocking = severity === 'blocking'
  const label = blocking ? t('specifications.check.blocking') : t('specifications.check.warnings')
  return (
    <div>
      <h3 className={`mb-1 text-xs font-semibold uppercase tracking-wider ${blocking ? 'text-red-300' : 'text-amber-300'}`}>{label}</h3>
      <ul aria-label={label} className="space-y-1">
        {own.map((finding, i) => (
          <li key={`${finding.where}-${finding.code}-${i}`} className="flex flex-wrap items-center gap-2 text-sm text-gray-200">
            <CircleAlert size={14} className={blocking ? 'text-red-300' : 'text-amber-300'} aria-hidden="true" />
            <Where where={finding.where} />
            <span>{finding.message}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
