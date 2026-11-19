import { useTranslation } from 'react-i18next'
import { useForgePolicy, useSetForgePolicy } from '../hooks/useDuties'
import { errorDetail, type ForgePolicy } from '../services/api'
import { Panel } from './ui'

const POLICIES: ForgePolicy[] = ['none', 'fetch', 'publish']

/**
 * Whether the company's git may contact a forge (issue #727): `none` keeps it
 * on the computer that holds the project, `fetch` reads the base from its
 * remote, `publish` also lets a Manager push and open the request. Any member
 * reads it; only an Owner changes it, each change audited. The deployment may
 * cap it (`FORGE_POLICY_MAX`), and a value above the cap cannot be chosen.
 */
export default function ForgePolicyPanel({ companyId, isOwner }: { companyId: number | null; isOwner: boolean }) {
  const { t } = useTranslation()
  const setting = useForgePolicy(companyId)
  const change = useSetForgePolicy(companyId)

  if (setting.isError) return <div role="alert" className="alert-error text-sm">{errorDetail(setting.error)}</div>
  if (!setting.data) return null
  const { forge_policy: stored, effective, ceiling } = setting.data
  const allowed = POLICIES.slice(0, POLICIES.indexOf(ceiling) + 1)
  return (
    <Panel title={t('forgePolicy.title')}>
      <fieldset className="space-y-3 p-3" disabled={!isOwner || change.isPending}>
        <legend className="sr-only">{t('forgePolicy.title')}</legend>
        <p className="text-xs text-gray-400">{t('forgePolicy.hint')}</p>
        {change.isError && <div role="alert" className="alert-error text-sm">{errorDetail(change.error)}</div>}
        {POLICIES.map(policy => (
          <label key={policy} className="flex items-start gap-2 text-[13px] text-gray-200">
            <input
              type="radio"
              name="forge-policy"
              className="mt-0.5 h-4 w-4 flex-shrink-0 accent-brand-500"
              value={policy}
              checked={stored === policy}
              disabled={!allowed.includes(policy)}
              onChange={() => change.mutate(policy)}
            />
            <span>
              <span className="font-medium">{t(`forgePolicy.${policy}.label`)}</span>
              <span className="block text-xs text-gray-400">{t(`forgePolicy.${policy}.hint`)}</span>
            </span>
          </label>
        ))}
        {effective !== stored && (
          <p role="note" className="text-xs text-amber-300">{t('forgePolicy.capped', { ceiling, effective })}</p>
        )}
        {!isOwner && <p className="text-xs text-gray-400">{t('forgePolicy.ownerOnly')}</p>}
      </fieldset>
    </Panel>
  )
}
