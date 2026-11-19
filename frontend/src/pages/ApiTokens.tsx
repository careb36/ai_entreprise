import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Ban, CircleCheck, Hourglass, KeyRound, type LucideIcon } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { errorDetail } from '../services/api'
import { tokenState, useApiTokens, useRevokeApiToken, type ApiToken, type CreatedApiToken, type TokenState } from '../services/apiTokens'
import { useAppStore } from '../store/useAppStore'
import { CountPill, EmptyState, LoadError, LoadingRows, PageHeader, Panel, Spinner, StatusBadge, type StatusTone, TABLE, TD, TH, TR } from '../components/ui'
import { CreateTokenForm, NewTokenNotice } from './apiTokens/CreateTokenForm'

const STATE_LOOK: Record<TokenState, { icon: LucideIcon; tone: StatusTone }> = {
  active: { icon: CircleCheck, tone: 'success' },
  expired: { icon: Hourglass, tone: 'neutral' },
  revoked: { icon: Ban, tone: 'danger' },
}

/** An ISO time as a date and time in the reader's language; a dash when there is none. */
export function formatWhen(value: string | null, locale: string): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

/**
 * API tokens (`/company/api-tokens`): the company's credentials for scripts
 * and CI. Every member sees the list; a Manager or an Owner creates a token
 * (its role never above their own; it always expires) and revokes one. The
 * backend enforces both; this page only hides what the role cannot do.
 */
export default function ApiTokens() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'
  const tokens = useApiTokens(companyId)
  const [created, setCreated] = useState<CreatedApiToken | null>(null)
  const rows = tokens.data?.tokens ?? []

  return (
    <div className="page-container max-w-5xl space-y-4">
      <PageHeader title={t('apiTokens.title')} subtitle={t('apiTokens.subtitle')} />
      {created && <NewTokenNotice token={created} onDismiss={() => setCreated(null)} />}
      {canManage && role && <CreateTokenForm ownRole={role} onCreated={setCreated} />}
      <Panel
        title={t('apiTokens.listTitle')}
        count={rows.length > 0 ? <CountPill count={rows.length} label={t('apiTokens.count', { count: rows.length })} /> : undefined}>
        {tokens.isLoading ? (
          <div className="p-3"><LoadingRows rows={2} label={t('apiTokens.loading')} /></div>
        ) : tokens.isError ? (
          <div className="p-3"><LoadError onRetry={() => tokens.refetch()}>{t('apiTokens.loadError', { message: errorDetail(tokens.error) })}</LoadError></div>
        ) : rows.length === 0 ? (
          <EmptyState icon={KeyRound} title={t('apiTokens.empty')} description={canManage ? t('apiTokens.emptyManager') : undefined} />
        ) : (
          <div className="overflow-x-auto">
            <table className={TABLE}>
              <caption className="sr-only">{t('apiTokens.listTitle')}</caption>
              <thead>
                <tr>
                  <th scope="col" className={TH}>{t('apiTokens.columns.name')}</th>
                  <th scope="col" className={TH}>{t('apiTokens.columns.role')}</th>
                  <th scope="col" className={TH}>{t('apiTokens.columns.state')}</th>
                  <th scope="col" className={TH}>{t('apiTokens.columns.createdBy')}</th>
                  <th scope="col" className={TH}>{t('apiTokens.columns.expires')}</th>
                  <th scope="col" className={TH}>{t('apiTokens.columns.lastUsed')}</th>
                  {canManage && <th scope="col" className={TH}><span className="sr-only">{t('apiTokens.columns.actions')}</span></th>}
                </tr>
              </thead>
              <tbody>
                {rows.map(token => <TokenRow key={token.id} token={token} canManage={canManage} />)}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  )
}

function TokenRow({ token, canManage }: { token: ApiToken; canManage: boolean }) {
  const { t, i18n } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const revoke = useRevokeApiToken(companyId)
  const [confirming, setConfirming] = useState(false)
  const state = tokenState(token)
  const look = STATE_LOOK[state]
  const locale = i18n.language

  return (
    <tr className={TR}>
      <td className={`${TD} font-medium`}>
        {token.name}
        {token.scopes.length > 0 && (
          <span className="mt-0.5 flex flex-wrap gap-1">{token.scopes.map(scope => <code key={scope} className="chip font-mono">{scope}</code>)}</span>
        )}
      </td>
      <td className={TD}>{t(`members.roles.${token.role}`)}</td>
      <td className={TD}>
        <StatusBadge icon={look.icon} tone={look.tone} label={t(`apiTokens.state.${state}`)} />
        {state === 'revoked' && <span className="text-meta mt-0.5 block tabular-nums">{formatWhen(token.revoked_at, locale)}</span>}
      </td>
      <td className={`${TD} text-gray-300`}>
        {token.created_by ?? '—'}
        <span className="text-meta block tabular-nums">{formatWhen(token.created_at, locale)}</span>
      </td>
      <td className={`${TD} tabular-nums text-gray-300`}>{token.expires_at ? formatWhen(token.expires_at, locale) : t('apiTokens.never')}</td>
      <td className={`${TD} tabular-nums text-gray-300`}>{token.last_used_at ? formatWhen(token.last_used_at, locale) : t('apiTokens.neverUsed')}</td>
      {canManage && (
        <td className={`${TD} text-right`}>
          {state === 'active' && (confirming ? (
            <span role="group" aria-label={t('apiTokens.confirmLabel', { name: token.name })} className="inline-flex flex-wrap items-center justify-end gap-1">
              <span className="text-xs text-amber-300">{t('apiTokens.confirmRevoke')}</span>
              <button type="button" className="btn-tool border border-red-700/60 text-red-300" disabled={revoke.isPending}
                onClick={() => revoke.mutate(token.id, { onSettled: () => setConfirming(false) })}>
                {revoke.isPending && <Spinner size={13} />}{t('apiTokens.revokeConfirm')}
              </button>
              <button type="button" className="btn-tool" onClick={() => setConfirming(false)}>{t('common.cancel')}</button>
            </span>
          ) : (
            <button type="button" className="btn-tool" onClick={() => setConfirming(true)}
              aria-label={t('apiTokens.revokeNamed', { name: token.name })}>
              <Ban size={13} aria-hidden="true" />{t('apiTokens.revoke')}
            </button>
          ))}
          {revoke.isError && <span role="alert" className="mt-1 block text-xs text-red-300">{errorDetail(revoke.error)}</span>}
        </td>
      )}
    </tr>
  )
}
