/**
 * The company's specifications: what the agents build from. Each acceptance
 * criterion becomes a test, and a sprint planned from an approved one says
 * which task covers each criterion. A Manager asks the PO for a draft from a
 * brief, or writes one; a Viewer reads.
 */
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Building2, ClipboardList, Pencil, Sparkles } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { errorDetail } from '../services/api'
import { type Specification, useCreateSpecification, useDraftSpecification, useSpecifications } from '../services/specifications'
import { SpecificationEditor } from '../components/SpecificationEditor'
import { CheckSummary, StatusBadge } from '../components/SpecificationBadges'
import { LoadErrorAlert, LoadingLine, Spinner } from '../components/knowledge/PageStates'
import { CountPill, EmptyState, PageHeader, Panel } from '../components/ui'
import { useAppStore } from '../store/useAppStore'

// Re-exported for the pages that import them from here.
export { CheckSummary, StatusBadge }

export function specificationPath(id: number) {
  return `/company/specifications/${id}`
}

function SpecificationRow({ specification }: { specification: Specification }) {
  const { t } = useTranslation()
  return (
    <li className="data-row grid gap-x-4 gap-y-1.5 sm:grid-cols-[minmax(0,1fr)_7rem_auto] sm:items-center">
      <div className="min-w-0">
        <Link to={specificationPath(specification.id)} className="block truncate rounded-sm text-[13px] font-semibold text-gray-100 hover:text-brand-300">
          {specification.title}
        </Link>
        <p className="text-meta mt-0.5 flex flex-wrap gap-x-2">
          <span className="tabular-nums">{t('specifications.version', { version: specification.version })}</span>
          {specification.drafted_by && <span>{t('specifications.draftedBy', { who: specification.drafted_by })}</span>}
        </p>
      </div>
      <StatusBadge status={specification.status} />
      <span className="sm:text-right"><CheckSummary check={specification.check} /></span>
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
    <Panel title={t('specifications.askPO')}>
      <form onSubmit={e => { e.preventDefault(); submit() }} className="space-y-3 p-3">
        <p className="max-w-prose text-xs text-gray-400">{t('specifications.briefHint')}</p>
        <label className="block">
          <span className="input-label">{t('specifications.briefLabel')}</span>
          <textarea value={brief} onChange={e => setBrief(e.target.value)} rows={4} maxLength={4000} required className="input w-full"
            placeholder={t('specifications.briefPlaceholder')} />
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" disabled={draft.isPending || !brief.trim()} className="btn btn-primary btn-sm">
            {draft.isPending ? <Spinner /> : <Sparkles size={14} aria-hidden="true" />}
            {draft.isPending ? t('specifications.drafting') : t('specifications.askPO')}
          </button>
          {draft.isError && (
            <span role="alert" className="text-[13px] text-red-300">{t('specifications.draftError', { message: errorDetail(draft.error) })}</span>
          )}
        </div>
      </form>
    </Panel>
  )
}

function WriteYourself({ companyId, onClose }: { companyId: number; onClose: () => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const create = useCreateSpecification()
  return (
    <section aria-labelledby="specifications-write" className="space-y-2">
      <h2 id="specifications-write" className="panel-title">{t('specifications.editor.newTitle')}</h2>
      <SpecificationEditor
        saving={create.isPending}
        error={create.isError ? errorDetail(create.error) : null}
        onCancel={() => { create.reset(); onClose() }}
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
  const [writing, setWriting] = useState(false)
  const ready = companyId !== null && !query.isLoading && !query.isError

  return (
    <div className="page-container max-w-5xl space-y-4">
      <PageHeader title={t('specifications.title')} subtitle={t('specifications.subtitle')}
        actions={ready && canWrite && !writing ? (
          <button type="button" onClick={() => setWriting(true)} className="btn-tool">
            <Pencil size={13} aria-hidden="true" /> {t('specifications.writeYourself')}
          </button>
        ) : undefined} />

      {companyId === null ? (
        <EmptyState icon={Building2} title={t('specifications.selectCompany')} dashed
          actions={<Link to="/companies/select" className="btn-tool">{t('workspace.selectCompany', { defaultValue: 'Select company' })}</Link>} />
      ) : query.isLoading ? (
        <LoadingLine>{t('specifications.loading')}</LoadingLine>
      ) : query.isError ? (
        <LoadErrorAlert onRetry={() => query.refetch()}>{t('specifications.loadError', { message: errorDetail(query.error) })}</LoadErrorAlert>
      ) : (
        <>
          {canWrite && writing && <WriteYourself companyId={companyId} onClose={() => setWriting(false)} />}
          {specifications.length === 0 ? (
            <EmptyState icon={ClipboardList} title={t('specifications.empty')} dashed
              description={canWrite ? t('specifications.emptyHint') : t('specifications.emptyViewer')} />
          ) : (
            <Panel title={t('specifications.listTitle')}
              count={<CountPill count={specifications.length} label={t('specifications.countLabel', { count: specifications.length })} />}>
              <ul className="data-list" aria-label={t('specifications.title')}>
                {specifications.map(specification => <SpecificationRow key={specification.id} specification={specification} />)}
              </ul>
            </Panel>
          )}
          {canWrite && <AskThePO companyId={companyId} />}
        </>
      )}
    </div>
  )
}
