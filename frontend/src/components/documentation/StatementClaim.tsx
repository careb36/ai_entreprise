/**
 * One person statement shown as what it is: a claim by its author, never an
 * observation. Dashed, under a speech mark, with who wrote it, in which role
 * and when (all recorded by the backend), and the digest the backend computed.
 * A withdrawal is shown as such, with who withdrew it.
 */
import { useTranslation } from 'react-i18next'
import { MessageSquareQuote } from 'lucide-react'
import { READABLE_TEXT } from '../knowledge/PageStates'
import type { PersonStatement } from '../../services/documentation'

export function StatementClaim({ statement }: { statement: PersonStatement }) {
  const { t } = useTranslation()
  const who = { author: statement.author, role: statement.author_role }
  return (
    <figure className="rounded-md border border-dashed border-surface-600 px-3 py-2" data-testid="person-statement">
      <figcaption className="text-meta flex flex-wrap items-center gap-x-2 gap-y-1">
        <MessageSquareQuote size={12} aria-hidden="true" className="flex-shrink-0 text-gray-400" />
        <span>{statement.withdrawn ? t('documentation.personSections.withdrawnBy', who) : t('documentation.personSections.claimBy', who)}</span>
        {statement.recorded_at && (
          <time className="tabular-nums" dateTime={statement.recorded_at}>{new Date(statement.recorded_at).toLocaleString()}</time>
        )}
        <span className="font-mono text-[11px] text-gray-400" title={t('documentation.personSections.digest')}>
          {statement.statement_sha256.slice(0, 12)}
        </span>
      </figcaption>
      {statement.withdrawn
        ? <p className="mt-1 text-xs italic text-gray-400">{t('documentation.personSections.withdrawn')}</p>
        : <blockquote className={`mt-1 ${READABLE_TEXT}`}>{statement.text}</blockquote>}
    </figure>
  )
}
