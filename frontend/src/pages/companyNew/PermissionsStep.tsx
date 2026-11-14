import type { Dispatch, SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import { approvalToggles } from './constants'
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
        <h2 className="text-lg font-semibold text-white mb-1">{t('companyNew.infraPerms')}</h2>
        <p className="text-sm text-gray-400">
          {t('companyNew.permissionsDescription')}
        </p>
      </div>

      <div className="card divide-y divide-surface-700 rounded-xl overflow-hidden">
        <PermRow icon="🔀" label={t('companyNew.resources.git.label')} permKey="git_access" value={permissions.git_access}
          options={gitOptions} onChange={updatePerm}
          desc={t('companyNew.resources.git.description')} />
        <PermRow icon="☁️" label={t('companyNew.resources.cloud.label')} permKey="cloud_access" value={permissions.cloud_access}
          options={cloudOptions} onChange={updatePerm}
          desc={t('companyNew.resources.cloud.description')} />
        <PermRow icon="🗄️" label={t('companyNew.resources.db.label')} permKey="db_access" value={permissions.db_access}
          options={dbOptions} onChange={updatePerm}
          desc={t('companyNew.resources.db.description')} />
        <PermRow icon="🔧" label={t('companyNew.resources.ci.label')} permKey="ci_access" value={permissions.ci_access}
          options={ciOptions} onChange={updatePerm}
          desc={t('companyNew.resources.ci.description')} />
        <PermRow icon="🔑" label={t('companyNew.resources.secrets.label')} permKey="secrets_access" value={permissions.secrets_access}
          options={secretOptions} onChange={updatePerm}
          desc={t('companyNew.resources.secrets.description')} />
      </div>

      <div className="card p-4 space-y-3">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">{t('companyNew.governance')}</p>
        {approvalToggles(t).map(({ key, label, icon }) => (
          <label key={key} className="flex items-center gap-3 cursor-pointer group">
            <input type="checkbox"
              checked={permissions[key as keyof Permissions] as boolean}
              onChange={e => setPermissions(prev => ({ ...prev, [key]: e.target.checked }))}
              className="w-4 h-4 rounded accent-brand-500 flex-shrink-0" />
            <span className="text-sm">
              <span className="mr-1.5">{icon}</span>
              <span className="text-gray-300 group-hover:text-white transition-colors">{label}</span>
            </span>
          </label>
        ))}
      </div>

      {autonomyLevel === 1 && (
        <div className="alert-info text-xs">
          <span>ℹ️</span>
          <span>{t('companyNew.manualApprovalHint')}</span>
        </div>
      )}
    </div>
  )
}
