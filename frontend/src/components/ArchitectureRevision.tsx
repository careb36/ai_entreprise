import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { GitBranchPlus } from 'lucide-react'
import { useReviseArchitecture } from '../services/architectureFiles'
import { architecturePath, useCompanyArchitecture } from '../services/architectures'
import { errorDetail } from '../services/api'
import { Spinner } from './ui'

/**
 * For a Manager, on an approved specification with no architecture of its
 * own: open the next version of the company's architecture for it
 * (`POST /v1/companies/{id}/architecture/revisions`), which carries the
 * current version over and keeps what it covers for earlier specifications.
 * Offered only once the company has an approved architecture; while another
 * revision is in draft, it links to that draft instead (a refusal because
 * one was opened meanwhile refreshes the company's versions, which shows it).
 */
export function ArchitectureRevision({ companyId, specificationId }: { companyId: number; specificationId: number }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const company = useCompanyArchitecture(companyId)
  const revise = useReviseArchitecture(companyId)
  const current = company.data?.current
  const draft = company.data?.draft
  if (!current) return null

  if (draft) {
    return (
      <p className="text-xs text-gray-400">
        {t('architectureFiles.revisionOpen', { version: draft.version })}{' '}
        <Link to={architecturePath(draft.specification_id, draft.id)} className="text-brand-300 hover:underline">{t('architectureFiles.openDraft')}</Link>
      </p>
    )
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" className="btn-tool border border-surface-700" disabled={revise.isPending}
        onClick={() => revise.mutate(specificationId, { onSuccess: architecture => navigate(architecturePath(architecture.specification_id, architecture.id)) })}>
        {revise.isPending ? <Spinner size={13} /> : <GitBranchPlus size={13} aria-hidden="true" />}
        {t('architectureFiles.revise', { version: current.version })}
      </button>
      <span className="text-meta">{t('architectureFiles.reviseHint')}</span>
      {revise.isError && <span role="alert" className="text-xs text-red-300">{errorDetail(revise.error)}</span>}
    </div>
  )
}
