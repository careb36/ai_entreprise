/**
 * The company's specifications: what the agents build from. Each acceptance
 * criterion becomes a test, and a sprint planned from an approved one says
 * which task covers each criterion. A Manager asks the PO for a draft from a
 * brief, or writes one; a Viewer reads.
 */
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ClipboardList, Loader2, Pencil, Sparkles } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { errorDetail } from '../services/api'
import { type Specification, useCreateSpecification, useDraftSpecification, useSpecifications } from '../services/specifications'
import { SpecificationEditor } from '../components/SpecificationEditor'
import { CheckSummary, StatusBadge } from '../components/SpecificationBadges'
import { useAppStore } from '../store/useAppStore'

// Re-exported for the pages that import them from here.
export { CheckSummary, StatusBadge }

export function specificationPath(id: number) {
  return `/company/specifications/${id}`
}

function SpecificationRow({ specification }: { specification: Specification }) {
  const { t } = useTranslation()
  return (
    <li className="card flex flex-wrap items-center gap-2 p-4 text-xs text-gray-400">
      <Link to={specificationPath(specification.id)} className="text-sm font-semibold text-white hover:text-brand-300">
        {specification.title}
      </Link>
      <span>{t('specifications.version', { version: specification.version })}</span>
      <StatusBadge status={specification.status} />
      {specification.drafted_by && <span>{t('specifications.draftedBy', { who: specification.drafted_by })}</span>}
      <span className="ml-auto"><CheckSummary check={specification.check} /></span>
    </li>
  )
}

function AskThePO({ companyId }: { companyId: number }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const draft = useDraftSpecification()
  const [brief, setBrief] = useState('')

  function submit() {
    if (!brief.trim() || draft.isPending) return
    draft.mutate({ company_id: companyId, brief: brief.trim() }, { onSuccess: specification => navigate(specificationPath(specification.id)) })
  }

  return (
    <section aria-labelledby="specifications-ask" className="card p-4 space-y-3">
      <h2 id="specifications-ask" className="text-sm font-semibold text-white">{t('specifications.askPO')}</h2>
      <p className="text-xs text-gray-500">{t('specifications.briefHint')}</p>
      <form onSubmit={e => { e.preventDefault(); submit() }} className="space-y-3">
        <label className="block">
          <span className="input-label">{t('specifications.briefLabel')}</span>
          <textarea value={brief} onChange={e => setBrief(e.target.value)} rows={4} maxLength={4000} required className="input w-full"
            placeholder={t('specifications.briefPlaceholder')} />
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" disabled={draft.isPending || !brief.trim()} className="btn btn-primary">
            {draft.isPending ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Sparkles size={15} aria-hidden="true" />}
            {draft.isPending ? t('specifications.drafting') : t('specifications.askPO')}
          </button>
          {draft.isError && (
            <span role="alert" className="text-sm text-red-300">{t('specifications.draftError', { message: errorDetail(draft.error) })}</span>
          )}
        </div>
      </form>
    </section>
  )
}

function WriteYourself({ companyId }: { companyId: number }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const create = useCreateSpecification()
  const [writing, setWriting] = useState(false)

  if (!writing) {
    return (
      <button type="button" onClick={() => setWriting(true)} className="btn btn-secondary">
        <Pencil size={15} aria-hidden="true" /> {t('specifications.writeYourself')}
      </button>
    )
  }
  return (
    <section aria-labelledby="specifications-write" className="space-y-2">
      <h2 id="specifications-write" className="text-sm font-semibold text-white">{t('specifications.editor.newTitle')}</h2>
      <SpecificationEditor
        saving={create.isPending}
        error={create.isError ? errorDetail(create.error) : null}
        onCancel={() => { create.reset(); setWriting(false) }}
        onSave={body => create.mutate({ company_id: companyId, ...body }, { onSuccess: specification => navigate(specificationPath(specification.id)) })}
      />
    </section>
  )
}

export default function Specifications() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const canWrite = role === 'Owner' || role === 'Manager'
  const query = useSpecifications(companyId)
  const specifications = query.data?.specifications ?? []

  return (
    <div className="page-container max-w-4xl space-y-6">
      <div className="page-header">
        <div>
          <div className="flex items-center gap-2">
            <ClipboardList className="text-brand-400" size={22} aria-hidden="true" />
            <h1 className="page-title">{t('specifications.title')}</h1>
          </div>
          <p className="text-gray-400 mt-1">{t('specifications.subtitle')}</p>
        </div>
      </div>

      {companyId === null ? (
        <div className="empty-state">
          <p className="empty-state-title">{t('specifications.selectCompany')}</p>
          <Link to="/companies/select" className="btn btn-ghost">{t('workspace.selectCompany', { defaultValue: 'Select company' })}</Link>
        </div>
      ) : query.isLoading ? (
        <div role="status" className="flex items-center gap-2 text-sm text-gray-400">
          <Loader2 size={15} className="animate-spin" aria-hidden="true" /> {t('specifications.loading')}
        </div>
      ) : query.isError ? (
        <div role="alert" className="alert-error flex flex-wrap items-center gap-2 text-sm">
          <span>{t('specifications.loadError', { message: errorDetail(query.error) })}</span>
          <button type="button" onClick={() => query.refetch()} className="underline">{t('common.retry')}</button>
        </div>
      ) : (
        <>
          {specifications.length === 0 ? (
            <div className="empty-state rounded-card border border-dashed border-surface-700">
              <ClipboardList size={28} className="empty-state-icon" aria-hidden="true" />
              <p className="empty-state-title">{t('specifications.empty')}</p>
              <p className="empty-state-desc">{canWrite ? t('specifications.emptyHint') : t('specifications.emptyViewer')}</p>
            </div>
          ) : (
            <ul className="space-y-2" aria-label={t('specifications.title')}>
              {specifications.map(specification => <SpecificationRow key={specification.id} specification={specification} />)}
            </ul>
          )}
          {canWrite && (
            <>
              <AskThePO companyId={companyId} />
              <WriteYourself companyId={companyId} />
            </>
          )}
        </>
      )}
    </div>
  )
}
