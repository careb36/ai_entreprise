import { useTranslation } from 'react-i18next'
import { Loader2, MessageSquare } from 'lucide-react'
import ChatConnectionForm from '../components/ChatConnectionForm'
import { useChatLinkCode, useChatLinks, useChatSettings, useUnlinkChat } from '../hooks/useChat'
import { useCurrentRole } from '../hooks/useQueries'
import { errorDetail, type ChatProvider } from '../services/api'
import { useAppStore } from '../store/useAppStore'

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
    <div className="page-container max-w-2xl space-y-6">
      <div className="page-header">
        <div>
          <div className="flex items-center gap-2">
            <MessageSquare className="text-brand-400" size={22} />
            <h1 className="page-title">{t('chat.title')}</h1>
          </div>
          <p className="text-gray-400 mt-1">{t('chat.subtitle')}</p>
        </div>
      </div>
      {companyId !== null && <ChatLinksPanel companyId={companyId} />}
      {role === 'Owner' && companyId !== null && (
        settings.isLoading ? (
          <Loader2 className="animate-spin text-brand-400" size={20} />
        ) : settings.isError ? (
          <div role="alert" className="alert-error text-sm">{errorDetail(settings.error)}</div>
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

  if (links.isLoading) return <Loader2 className="animate-spin text-brand-400" size={20} />
  if (links.isError) return <div role="alert" className="alert-error text-sm">{errorDetail(links.error)}</div>
  const data = links.data
  if (!data) return null
  return (
    <section className="card p-4 space-y-3" aria-label={t('chat.linksTitle')}>
      <h2 className="text-lg font-semibold">{t('chat.linksTitle')}</h2>
      <p className="text-xs text-gray-400">{t('chat.linksHelp')}</p>
      <ul className="space-y-2">
        {PROVIDERS.map(provider => {
          const linked = data.links.find(l => l.provider === provider)
          return (
            <li key={provider} className="flex flex-wrap items-center gap-3 text-sm">
              <span className="font-medium w-16">{t(`chat.${provider}.name`)}</span>
              {linked ? (
                <>
                  <span className="text-gray-300">
                    {t('chat.linkedAs', { user: linked.display_name || linked.external_user_id, workspace: linked.workspace_id })}
                  </span>
                  <button type="button" className="btn btn-secondary" disabled={unlink.isPending} onClick={() => unlink.mutate(provider)}>
                    {t('chat.unlink')}
                  </button>
                </>
              ) : data.connected[provider] ? (
                <button type="button" className="btn btn-secondary" disabled={code.isPending} onClick={() => code.mutate(provider)}>
                  {t('chat.link', { provider: t(`chat.${provider}.name`) })}
                </button>
              ) : (
                <span className="text-gray-500">{t('chat.notConnected')}</span>
              )}
            </li>
          )
        })}
      </ul>
      {code.data && (
        <div role="status" className="text-sm text-gray-200">
          <p>{t('chat.codeIntro')}</p>
          <code className="block text-lg tracking-widest my-1">{code.data.code}</code>
          <p className="text-xs text-gray-400">{t(`chat.${code.data.provider}.codeHow`, { code: code.data.code })}</p>
        </div>
      )}
      {code.isError && <div role="alert" className="alert-error text-sm">{errorDetail(code.error)}</div>}
    </section>
  )
}
