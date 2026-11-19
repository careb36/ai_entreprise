import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { CircleCheck, KeyRound, Link2, Unlink } from 'lucide-react'
import { useMySsoIdentities, useStartSsoLink, useUnlinkSsoIdentity } from '../hooks/useSsoIdentities'
import { errorDetail } from '../services/api'
import { EmptyState, Panel } from './ui'

/**
 * The signed-in person's single sign-on identities (docs/design/SSO.md,
 * linking in a session): link one at a company's provider, or unlink one
 * while another way to sign in remains. The provider sends the browser back
 * here with `sso_link` or `sso_link_error`.
 */
export function SsoIdentities() {
  const { t } = useTranslation()
  const [params] = useSearchParams()
  const mine = useMySsoIdentities()
  const start = useStartSsoLink()
  const unlink = useUnlinkSsoIdentity()
  const linked = params.get('sso_link')
  const failed = params.get('sso_link_error')

  const message = (code: string) =>
    t(`account.sso.errors.${code}`, { defaultValue: t(`sso.errors.${code}`, { defaultValue: t('sso.errors.unknown') }) })

  if (mine.isLoading) return null
  if (mine.isError) return <div role="alert" className="alert-error text-sm">{errorDetail(mine.error)}</div>
  const data = mine.data
  if (!data || (data.identities.length === 0 && data.linkable.length === 0)) {
    return <EmptyState icon={KeyRound} title={t('account.sso.none')} dashed />
  }

  return (
    <Panel title={t('account.sso.title')}>
      <div className="space-y-3 p-3">
        <p className="text-xs text-gray-400">{t('account.sso.intro')}</p>
        {linked && (
          <p role="status" className="alert-success items-center text-sm">
            <CircleCheck size={15} aria-hidden="true" className="flex-shrink-0" />
            {t(`account.sso.outcome.${linked}`, { defaultValue: t('account.sso.outcome.linked') })}
          </p>
        )}
        {failed && <div role="alert" className="alert-error text-sm">{message(failed)}</div>}
        <ul className="space-y-2">
          {data.identities.map(identity => (
            <li key={identity.id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-surface-700 p-3">
              <div className="text-sm">
                <p className="text-gray-100">{identity.company_name ?? t('account.sso.unknownCompany')}</p>
                <p className="text-xs text-gray-400">
                  {identity.email}
                  {identity.linked_at && ` · ${t('account.sso.linkedAt', { date: new Date(identity.linked_at + 'Z').toLocaleDateString() })}`}
                </p>
              </div>
              <button type="button" className="btn-tool border border-surface-700" disabled={unlink.isPending}
                onClick={() => unlink.mutate(identity.id)}>
                <Unlink size={13} aria-hidden="true" />{t('account.sso.unlink')}
              </button>
            </li>
          ))}
          {data.linkable.map(company => (
            <li key={company.company_id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-dashed border-surface-700 p-3">
              <p className="text-sm text-gray-300">{company.company_name}</p>
              <button type="button" className="btn-tool border border-surface-700" disabled={start.isPending}
                onClick={() => start.mutate(company.company_id)}>
                <Link2 size={13} aria-hidden="true" />{t('account.sso.link')}
              </button>
            </li>
          ))}
        </ul>
        {unlink.isError && <div role="alert" className="alert-error text-sm">{message(errorDetail(unlink.error))}</div>}
        {start.isError && <div role="alert" className="alert-error text-sm">{message(errorDetail(start.error))}</div>}
      </div>
    </Panel>
  )
}
