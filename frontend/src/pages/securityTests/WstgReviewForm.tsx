import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ClipboardCheck } from 'lucide-react'
import { errorDetail } from '../../services/api'
import { useRecordWstgReview, type WstgResult, type WstgRow } from '../../services/securityTests'
import { useAppStore } from '../../store/useAppStore'
import { FormActions, Panel, SavedNote, Spinner } from '../../components/ui'

const RESULTS: WstgResult[] = ['pass', 'fail', 'not_applicable']

/** Today in the reader's calendar, as an ISO date: the latest a review may be dated. */
export function today(now: Date = new Date()): string {
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 10)
}

/**
 * A Manager records a person's review of one WSTG test: which test, the
 * result, who reviewed it and when (not in the future), the artifact it rests
 * on, and a note (required for "not applicable"). The backend computes the
 * record's digest and audits it.
 */
export function WstgReviewForm({ rows }: { rows: WstgRow[] }) {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const record = useRecordWstgReview(companyId)
  const ids = { test: useId(), result: useId(), name: useId(), date: useId(), artifact: useId(), note: useId() }
  // Tests waiting for a person first, then the rest.
  const ordered = [...rows].sort((a, b) => Number(b.status === 'needs-manual') - Number(a.status === 'needs-manual'))
  const [form, setForm] = useState({ wstg_id: ordered[0]?.id ?? '', result: 'pass' as WstgResult, reviewer_name: '', reviewed_on: today(), artifact: '', note: '' })
  const [saved, setSaved] = useState<string | null>(null)
  const set = (field: keyof typeof form) => (event: { target: { value: string } }) => setForm(current => ({ ...current, [field]: event.target.value }))
  const noteRequired = form.result === 'not_applicable'
  const ready = form.wstg_id && form.reviewer_name.trim() && form.reviewed_on && form.reviewed_on <= today() && form.artifact.trim() && (!noteRequired || form.note.trim())

  function submit() {
    if (!ready) return
    setSaved(null)
    record.mutate(
      { ...form, reviewer_name: form.reviewer_name.trim(), artifact: form.artifact.trim(), note: form.note.trim() || null },
      { onSuccess: review => { setSaved(review.wstg_id); setForm(current => ({ ...current, artifact: '', note: '' })) } },
    )
  }

  return (
    <Panel title={<span className="flex items-center gap-1.5"><ClipboardCheck size={13} aria-hidden="true" className="text-gray-400" />{t('securityTests.form.title')}</span>}>
      <form className="space-y-3 p-3" aria-label={t('securityTests.form.title')} onSubmit={event => { event.preventDefault(); submit() }}>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <label htmlFor={ids.test} className="input-label">{t('securityTests.form.test')}</label>
            <select id={ids.test} value={form.wstg_id} onChange={set('wstg_id')} className="select w-full" required>
              {ordered.map(row => <option key={row.id} value={row.id}>{`${row.short} — ${row.name}`}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor={ids.result} className="input-label">{t('securityTests.form.result')}</label>
            <select id={ids.result} value={form.result} onChange={set('result')} className="select w-full">
              {RESULTS.map(value => <option key={value} value={value}>{t(`securityTests.result.${value}`)}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor={ids.date} className="input-label">{t('securityTests.form.date')}</label>
            <input id={ids.date} type="date" required max={today()} value={form.reviewed_on} onChange={set('reviewed_on')} className="input w-full" />
          </div>
          <div>
            <label htmlFor={ids.name} className="input-label">{t('securityTests.form.reviewer')}</label>
            <input id={ids.name} required maxLength={120} value={form.reviewer_name} onChange={set('reviewer_name')} className="input w-full" autoComplete="name" />
          </div>
          <div>
            <label htmlFor={ids.artifact} className="input-label">{t('securityTests.form.artifact')}</label>
            <input id={ids.artifact} required maxLength={500} value={form.artifact} onChange={set('artifact')}
              placeholder={t('securityTests.form.artifactPlaceholder')} className="input w-full" />
          </div>
          <div className="sm:col-span-2">
            <label htmlFor={ids.note} className="input-label">{noteRequired ? t('securityTests.form.noteRequired') : t('securityTests.form.note')}</label>
            <textarea id={ids.note} rows={2} maxLength={1000} required={noteRequired} value={form.note} onChange={set('note')} className="input w-full" />
          </div>
        </div>
        <FormActions>
          <button type="submit" className="btn-tool border border-surface-700" disabled={record.isPending || !ready}>
            {record.isPending && <Spinner size={13} />}{t('securityTests.form.submit')}
          </button>
          {saved && <SavedNote>{t('securityTests.form.saved', { id: saved })}</SavedNote>}
          {record.isError && <span role="alert" className="text-xs text-red-300">{t('securityTests.form.error', { message: errorDetail(record.error) })}</span>}
        </FormActions>
      </form>
    </Panel>
  )
}
