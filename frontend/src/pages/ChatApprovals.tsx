import { useTranslation } from 'react-i18next'
import { Link2, MessageSquare, Unplug } from 'lucide-react'
import ChatConnectionForm from '../components/ChatConnectionForm'
import { useChatLinkCode, useChatLinks, useChatSettings, useUnlinkChat } from '../hooks/useChat'
import { useCurrentRole } from '../hooks/useQueries'
import { errorDetail, type ChatProvider } from '../services/api'
import { useAppStore } from '../store/useAppStore'
import { EmptyState, PageHeader, Panel, StatusBadge } from '../components/ui'
import { LoadError, LoadingRows } from './admin/AdminParts'

const PROVIDERS: ChatProvider[] = ['slack', 'teams']

/**
 * Approving from Slack or Teams. Every member links their own chat account
 * (a one-time code they type in chat); an Owner also connects the company's
 * Slack app and Teams webhooks below.
 */
export default function ChatApprovals() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const settings = useChatSettings(companyId, role === 'Owner')

  return (
    <div className="page-container max-w-3xl space-y-4">
      <PageHeader title={t('chat.title')} subtitle={t('chat.subtitle')} />
      {companyId === null && <EmptyState icon={MessageSquare} title={t('chat.selectCompany')} dashed />}
      {companyId !== null && <ChatLinksPanel companyId={companyId} />}
      {role === 'Owner' && companyId !== null && (
        settings.isLoading ? (
          <LoadingRows rows={2} />
        ) : settings.isError ? (
          <LoadError message={errorDetail(settings.error)} onRetry={() => settings.refetch()} />
        ) : settings.data ? (
          PROVIDERS.map(provider => (
            <ChatConnectionForm
              key={`${provider}-${settings.data.connections[provider]?.updated_at ?? 'new'}`}
              companyId={companyId}
              provider={provider}
              initial={settings.data.connections[provider]}
            />
          ))
        ) : null
      )}
    </div>
  )
}

function ChatLinksPanel({ companyId }: { companyId: number }) {
  const { t } = useTranslation()
  const links = useChatLinks(companyId)
  const code = useChatLinkCode(companyId)
  const unlink = useUnlinkChat(companyId)

  if (links.isLoading) return <LoadingRows rows={1} />
  if (links.isError) return <LoadError message={errorDetail(links.error)} onRetry={() => links.refetch()} />
  const data = links.data
  if (!data) return null
  return (
    <Panel title={t('chat.linksTitle')}>
      <p className="px-3 pt-3 text-xs text-gray-400">{t('chat.linksHelp')}</p>
      <ul className="data-list mt-2 border-t border-surface-700">
        {PROVIDERS.map(provider => {
          const linked = data.links.find(l => l.provider === provider)
          return (
            <li key={provider} className="data-row flex flex-wrap items-center gap-3">
              <span className="w-16 text-[13px] font-semibold text-gray-100">{t(`chat.${provider}.name`)}</span>
              {linked ? (
                <>
                  <span className="inline-flex min-w-0 flex-1 items-center gap-1.5 text-xs text-gray-200">
                    <Link2 size={13} aria-hidden="true" className="flex-shrink-0 text-emerald-300" />
                    {t('chat.linkedAs', { user: linked.display_name || linked.external_user_id, workspace: linked.workspace_id })}
                  </span>
                  <button type="button" className="btn-tool border border-surface-700" disabled={unlink.isPending} onClick={() => unlink.mutate(provider)}>
                    {t('chat.unlink')}
                  </button>
                </>
              ) : data.connected[provider] ? (
                <button type="button" className="btn-tool border border-surface-700" disabled={code.isPending} onClick={() => code.mutate(provider)}>
                  <Link2 size={13} aria-hidden="true" />{t('chat.link', { provider: t(`chat.${provider}.name`) })}
                </button>
              ) : (
                <StatusBadge icon={Unplug} label={t('chat.notConnected')} />
              )}
            </li>
          )
        })}
      </ul>
      {(code.data || code.isError) && (
        <div className="border-t border-surface-700 p-3">
          {code.data && (
            <div role="status" className="text-sm text-gray-200">
              <p>{t('chat.codeIntro')}</p>
              <code className="my-1 block font-mono text-lg tracking-widest text-gray-50">{code.data.code}</code>
              <p className="text-xs text-gray-400">{t(`chat.${code.data.provider}.codeHow`, { code: code.data.code })}</p>
            </div>
          )}
          {code.isError && <div role="alert" className="alert-error text-sm">{errorDetail(code.error)}</div>}
        </div>
      )}
    </Panel>
  )
}
