import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useCompanyDeletion, useDeleteCompany, useRenameCompany } from '../hooks/useCompanyAdmin'
import { errorDetail } from '../services/api'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'
import { Panel } from './ui'

/**
 * The company's name, and deleting the company (`routers/company_admin.py`).
 * Only an Owner renames it or deletes it. Deleting lists what goes, is held
 * back while a sprint or an owner action runs, and needs the name typed back:
 * it removes every row the company holds and cannot be undone. The project
 * folder and its branches stay on the computer that holds them.
 */
export default function CompanyAdminPanel({ companyId, isOwner }: { companyId: number; isOwner: boolean }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const companyName = useAppStore(state => state.companyName) ?? ''
  const setCompany = useAppStore(state => state.setCompany)
  const loadMe = useAuthStore(state => state.loadMe)
  const [name, setName] = useState(companyName)
  const [deleting, setDeleting] = useState(false)
  const [typed, setTyped] = useState('')
  const rename = useRenameCompany(companyId)
  const remove = useDeleteCompany(companyId)
  const preview = useCompanyDeletion(companyId, deleting && isOwner)

  const saveName = () => rename.mutate(name, {
    onSuccess: data => {
      setCompany(companyId, data.company_name)
      setName(data.company_name)
      void loadMe()
    },
  })
  const confirmDelete = () => remove.mutate(typed, {
    onSuccess: () => {
      setCompany(null, null)
      void loadMe()
      navigate('/companies/select')
    },
  })
  const rows = Object.entries(preview.data?.rows ?? {}).sort((a, b) => b[1] - a[1])
  const blockers = preview.data?.blockers ?? []
  const trimmed = name.trim()

  return (
    <Panel title={t('companyAdmin.title')}>
      <div className="space-y-4 p-3">
        <form className="space-y-1" onSubmit={event => { event.preventDefault(); saveName() }}>
          <label htmlFor="company-name" className="text-meta">{t('companyAdmin.name')}</label>
          <div className="flex gap-2">
            <input id="company-name" type="text" className="input w-full" maxLength={100}
              value={name} disabled={!isOwner || rename.isPending} onChange={event => setName(event.target.value)} />
            <button type="submit" className="btn-secondary"
              disabled={!isOwner || rename.isPending || !trimmed || trimmed === companyName}>
              {t('companyAdmin.rename')}
            </button>
          </div>
          {rename.isError && <div role="alert" className="alert-error text-sm">{errorDetail(rename.error)}</div>}
          {rename.isSuccess && <p role="status" className="text-xs text-emerald-300">{t('companyAdmin.renamed')}</p>}
        </form>

        <div className="space-y-2 border-t border-surface-700 pt-3">
          <p className="text-[13px] font-medium text-red-300">{t('companyAdmin.deleteTitle')}</p>
          <p className="text-xs text-gray-400">{t('companyAdmin.deleteHint')}</p>
          {!deleting ? (
            <button type="button" className="btn-danger" disabled={!isOwner} onClick={() => setDeleting(true)}>
              {t('companyAdmin.deleteStart')}
            </button>
          ) : (
            <div className="space-y-2">
              {preview.isError && <div role="alert" className="alert-error text-sm">{errorDetail(preview.error)}</div>}
              {rows.length > 0 && (
                <ul aria-label={t('companyAdmin.rowsLabel')} className="grid grid-cols-2 gap-x-4 text-xs text-gray-300 md:grid-cols-3">
                  {rows.map(([table, n]) => (
                    <li key={table} className="flex justify-between gap-2"><span className="truncate">{table}</span><span className="tabular-nums">{n}</span></li>
                  ))}
                </ul>
              )}
              {blockers.map(b => (
                <p key={b} role="note" className="text-xs text-amber-300">{t(`companyAdmin.blocker.${b}`, { defaultValue: b })}</p>
              ))}
              <label htmlFor="company-confirm" className="text-meta">{t('companyAdmin.typeName', { name: companyName })}</label>
              <input id="company-confirm" type="text" className="input w-full" autoComplete="off"
                value={typed} onChange={event => setTyped(event.target.value)} />
              {remove.isError && <div role="alert" className="alert-error text-sm">{errorDetail(remove.error)}</div>}
              <div className="flex gap-2">
                <button type="button" className="btn-danger"
                  disabled={remove.isPending || blockers.length > 0 || typed.trim() !== companyName}
                  onClick={confirmDelete}>
                  {t('companyAdmin.deleteConfirm')}
                </button>
                <button type="button" className="btn-secondary" onClick={() => { setDeleting(false); setTyped('') }}>
                  {t('common.cancel', { defaultValue: 'Cancel' })}
                </button>
              </div>
            </div>
          )}
        </div>
        {!isOwner && <p className="text-xs text-gray-400">{t('companyAdmin.ownerOnly')}</p>}
      </div>
    </Panel>
  )
}
