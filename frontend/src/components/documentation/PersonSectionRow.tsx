/**
 * One person section: its heading and what to write, the current statement as
 * a claim, its history on request, and for an Owner or a Manager (`canWrite`,
 * the role the backend requires for PUT and DELETE) a form to write it and a
 * way to withdraw it. A Viewer is shown neither: the backend refuses them
 * anyway, the UI only reflects it.
 */
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { History, PencilLine, Undo2 } from 'lucide-react'
import { errorDetail } from '../../services/api'
import { type PersonSection, useWithdrawPersonSection, useWritePersonSection } from '../../services/documentation'
import { PersonSectionHistory } from './PersonSectionHistory'
import { StatementClaim } from './StatementClaim'
import { Spinner } from '../ui'

/** The i18n key of a section (`charter.budget` -> `charter_budget`: i18next splits keys on dots). */
export const sectionKey = (key: string) => key.replace(/\./g, '_')

export function PersonSectionRow({ companyId, section, canWrite, textLimit }: {
  companyId: number
  section: PersonSection
  canWrite: boolean
  textLimit: number
}) {
  const { t } = useTranslation()
  const [editing, setEditing] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [draft, setDraft] = useState('')
  const [saved, setSaved] = useState(false)
  const write = useWritePersonSection()
  const withdraw = useWithdrawPersonSection()
  const name = t(`documentation.personSections.sections.${sectionKey(section.key)}.title`, { defaultValue: section.key })
  const what = t(`documentation.personSections.sections.${sectionKey(section.key)}.what`, { defaultValue: '' })
  const statement = section.statement
  const empty = draft.trim().length === 0

  function startEditing() {
    setDraft(statement?.text ?? '')
    setSaved(false)
    write.reset()
    setEditing(true)
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    if (empty) return
    write.mutate({ companyId, section: section.key, text: draft }, {
      onSuccess: () => { setEditing(false); setSaved(true) },
    })
  }

  function confirmWithdraw() {
    if (!window.confirm(t('documentation.personSections.confirmWithdraw', { section: name }))) return
    setSaved(false)
    withdraw.mutate({ companyId, section: section.key }, { onSuccess: () => setSaved(true) })
  }

  return (
    <li className="data-row space-y-2" data-section={section.key}>
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <h4 className="text-[13px] font-medium text-gray-100">{name}</h4>
          {what && <p className="text-xs text-gray-400">{what}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <button type="button" className={`btn-tool ${showHistory ? 'btn-tool-on' : ''}`} aria-expanded={showHistory}
            onClick={() => setShowHistory(open => !open)}>
            <History size={13} aria-hidden="true" />{t(showHistory ? 'documentation.personSections.hideHistory' : 'documentation.personSections.history')}
          </button>
          {canWrite && !editing && (
            <button type="button" className="btn-tool" onClick={startEditing}>
              <PencilLine size={13} aria-hidden="true" />{t(statement ? 'documentation.personSections.edit' : 'documentation.personSections.write')}
            </button>
          )}
          {canWrite && statement && !editing && (
            <button type="button" className="btn-tool" disabled={withdraw.isPending} onClick={confirmWithdraw}>
              {withdraw.isPending ? <Spinner size={13} /> : <Undo2 size={13} aria-hidden="true" />}{t('documentation.personSections.withdraw')}
            </button>
          )}
        </div>
      </div>

      {editing ? (
        <form className="space-y-2" onSubmit={submit}>
          <label className="block">
            <span className="sr-only">{t('documentation.personSections.textLabel', { section: name })}</span>
            <textarea value={draft} onChange={e => setDraft(e.target.value)} maxLength={textLimit} rows={4}
              className="input w-full text-sm" aria-describedby={`${section.key}-limit`} />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button type="submit" className="btn btn-primary btn-sm" disabled={empty || write.isPending}>
              {write.isPending && <Spinner />}{t('documentation.personSections.save')}
            </button>
            <button type="button" className="btn-tool" onClick={() => setEditing(false)}>{t('documentation.personSections.cancel')}</button>
            <span id={`${section.key}-limit`} className="text-meta tabular-nums">
              {t('documentation.personSections.characters', { count: draft.length, limit: textLimit })}
            </span>
          </div>
          <p className="text-xs text-gray-400">{t('documentation.personSections.claimNote')}</p>
          {write.isError && <p role="alert" className="text-[13px] text-red-300">{t('documentation.personSections.saveError', { message: errorDetail(write.error) })}</p>}
        </form>
      ) : statement ? (
        <StatementClaim statement={statement} />
      ) : (
        <p className="text-xs text-gray-400">{t('documentation.personSections.notFilled')}</p>
      )}

      {withdraw.isError && <p role="alert" className="text-[13px] text-red-300">{t('documentation.personSections.withdrawError', { message: errorDetail(withdraw.error) })}</p>}
      {saved && <p role="status" className="text-xs text-amber-300">{t('documentation.personSections.stale')}</p>}
      {showHistory && <PersonSectionHistory companyId={companyId} section={section.key} />}
    </li>
  )
}
