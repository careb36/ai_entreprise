import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { MessageSquarePlus } from 'lucide-react'
import { errorDetail } from '../services/api'
import { useDraftSpecification } from '../services/specifications'

/** As the backend bounds a brief (`spec_author.BRIEF_LIMIT`). */
export const REQUEST_LIMIT = 6000

/**
 * The client's request box (issue #331): what they need, in plain words. The
 * PO turns it into a draft specification (`POST /v1/specifications/draft`),
 * which stays the client's to approve at every autonomy level.
 */
export function ClientRequestBox({ companyId }: { companyId: number }) {
  const { t } = useTranslation()
  const [text, setText] = useState('')
  const draft = useDraftSpecification()
  const brief = text.trim()
  const title = t('manager.requestTitle', { defaultValue: 'What do you need?' })

  return (
    <section aria-label={title} className="card space-y-2 p-4 text-sm">
      <h2 className="font-semibold text-gray-200">{title}</h2>
      <p className="text-xs text-gray-400">
        {t('manager.requestHint', { defaultValue: 'Say it in plain words. The PO writes a draft specification from it, and approving it stays yours.' })}
      </p>
      <form
        className="space-y-2"
        onSubmit={event => {
          event.preventDefault()
          if (brief) draft.mutate({ company_id: companyId, brief })
        }}
      >
        <label className="sr-only" htmlFor="client-request">{t('manager.requestLabel', { defaultValue: 'Your request, in plain words' })}</label>
        <textarea
          id="client-request"
          className="input min-h-24 w-full"
          maxLength={REQUEST_LIMIT}
          value={text}
          onChange={event => setText(event.target.value)}
          placeholder={t('manager.requestPlaceholder', { defaultValue: 'For example: customers should be able to export their invoices as a spreadsheet.' })}
        />
        <button type="submit" className="btn btn-primary btn-sm" disabled={!brief || draft.isPending}>
          <MessageSquarePlus size={14} aria-hidden="true" />
          {draft.isPending
            ? t('manager.requestSending', { defaultValue: 'The PO is writing the draft…' })
            : t('manager.requestSend', { defaultValue: 'Ask the PO for a draft' })}
        </button>
      </form>
      {draft.isError && (
        <p role="alert" className="text-xs text-red-400">
          {t('manager.requestError', { message: errorDetail(draft.error), defaultValue: 'Could not draft it: {{message}}' })}
        </p>
      )}
      {draft.data && (
        <p role="status" className="text-xs text-emerald-300">
          <Link to={`/company/specifications/${draft.data.id}`} className="underline">
            {t('manager.requestDone', { title: draft.data.title, defaultValue: 'Read and approve the draft: {{title}}' })}
          </Link>
        </p>
      )}
    </section>
  )
}
