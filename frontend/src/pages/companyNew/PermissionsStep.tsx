import type { Dispatch, SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import { Info } from 'lucide-react'
import { approvalToggles } from './constants'
import { APPROVAL_ICONS, RESOURCE_ICONS } from './icons'
import { PermRow } from './PermRow'
import type { Permissions } from './types'

/** Step 3: what the agents may touch, bounded by the autonomy level, and the approvals. */
export function PermissionsStep({ autonomyLevel, permissions, updatePerm, setPermissions }: {
  autonomyLevel: number
  permissions: Permissions
  updatePerm: (key: string, val: string) => void
  setPermissions: Dispatch<SetStateAction<Permissions>>
}) {
  const { t } = useTranslation()
  const gitOptions    = ['read_only', 'write', ...(autonomyLevel === 3 ? ['admin'] : [])]
  const cloudOptions  = ['none', 'read_only', ...(autonomyLevel >= 2 ? ['write'] : []), ...(autonomyLevel === 3 ? ['admin'] : [])]
  const dbOptions     = ['read_only', 'write', ...(autonomyLevel === 3 ? ['admin'] : [])]
  const ciOptions     = ['none', 'read_only', ...(autonomyLevel >= 2 ? ['write'] : [])]
  const secretOptions = ['none', ...(autonomyLevel >= 2 ? ['read_only'] : []), ...(autonomyLevel === 3 ? ['write'] : [])]

  return (
    <div className="space-y-5">
      <div>
        <h2 className="mb-1 text-base font-semibold text-gray-50">{t('companyNew.infraPerms')}</h2>
        <p className="text-[13px] text-gray-400">
          {t('companyNew.permissionsDescription')}
        </p>
      </div>

      <ul className="data-list overflow-hidden rounded-lg border border-surface-700 bg-surface-950/30">
        <PermRow icon={RESOURCE_ICONS.git_access} label={t('companyNew.resources.git.label')} permKey="git_access" value={permissions.git_access}
          options={gitOptions} onChange={updatePerm}
          desc={t('companyNew.resources.git.description')} />
        <PermRow icon={RESOURCE_ICONS.cloud_access} label={t('companyNew.resources.cloud.label')} permKey="cloud_access" value={permissions.cloud_access}
          options={cloudOptions} onChange={updatePerm}
          desc={t('companyNew.resources.cloud.description')} />
        <PermRow icon={RESOURCE_ICONS.db_access} label={t('companyNew.resources.db.label')} permKey="db_access" value={permissions.db_access}
          options={dbOptions} onChange={updatePerm}
          desc={t('companyNew.resources.db.description')} />
        <PermRow icon={RESOURCE_ICONS.ci_access} label={t('companyNew.resources.ci.label')} permKey="ci_access" value={permissions.ci_access}
          options={ciOptions} onChange={updatePerm}
          desc={t('companyNew.resources.ci.description')} />
        <PermRow icon={RESOURCE_ICONS.secrets_access} label={t('companyNew.resources.secrets.label')} permKey="secrets_access" value={permissions.secrets_access}
          options={secretOptions} onChange={updatePerm}
          desc={t('companyNew.resources.secrets.description')} />
      </ul>

      <fieldset className="space-y-2.5 rounded-lg border border-surface-700 bg-surface-950/30 p-3">
        <legend className="px-1 text-[13px] font-semibold text-gray-100">{t('companyNew.governance')}</legend>
        {approvalToggles(t).map(({ key, label }) => {
          const Icon = APPROVAL_ICONS[key]
          return (
            <label key={key} className="group flex cursor-pointer items-center gap-2.5">
              <input type="checkbox"
                checked={permissions[key as keyof Permissions] as boolean}
                onChange={e => setPermissions(prev => ({ ...prev, [key]: e.target.checked }))}
                className="h-4 w-4 flex-shrink-0 rounded accent-brand-500" />
              {Icon && <Icon size={14} aria-hidden="true" className="flex-shrink-0 text-gray-400" />}
              <span className="text-[13px] text-gray-200 group-hover:text-white">{label}</span>
            </label>
          )
        })}
      </fieldset>

      {autonomyLevel === 1 && (
        <div className="alert-info items-center p-3 text-xs">
          <Info size={14} aria-hidden="true" className="flex-shrink-0" />
          <span>{t('companyNew.manualApprovalHint')}</span>
        </div>
      )}
    </div>
  )
}
