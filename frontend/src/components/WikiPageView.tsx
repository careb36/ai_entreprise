import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, History, Loader2, Pencil, X } from 'lucide-react'
import { useDecideWikiVersion, useWikiVersions, useWriteWikiPage } from '../hooks/useWiki'
import { errorDetail, type WikiBlock, type WikiPageDetail, type WikiSource, type WikiVersion } from '../services/api'
import { WikiMarkdown } from './WikiMarkdown'

export const WIKI_STATUS_STYLES: Record<string, string> = {
  current: 'bg-emerald-900/40 text-emerald-300 border-emerald-700/40',
  stale: 'bg-amber-900/40 text-amber-300 border-amber-700/40',
  proposed: 'bg-sky-900/40 text-sky-300 border-sky-700/40',
}

function sourceLabel(source: WikiSource): string {
  if (source.kind === 'file') return `${source.ref}@${(source.sha256 ?? '').slice(0, 12)}`
  if (['req', 'adr', 'mod'].includes(source.kind)) return source.ref
  return `${source.kind} #${source.ref}`
}

/** The sources a statement cites, as the backend resolved them: beside the claim, never inside it. */
function Sources({ sources }: { sources: WikiSource[] }) {
  const { t } = useTranslation()
  if (!sources.length) return null
  return (
    <ul aria-label={t('wiki.sources')} className="flex flex-wrap gap-1">
      {sources.map(source => (
        <li key={`${source.kind}:${source.ref}:${source.sha256 ?? ''}`}
          className={`badge border font-mono text-[10px] ${source.known ? 'border-surface-600 text-gray-300' : 'border-red-700/50 text-red-300'}`}
          title={source.title ?? undefined}>
          {sourceLabel(source)}
          {source.verdict ? ` · ${t(`wiki.verdict.${source.verdict}`, { defaultValue: source.verdict })}` : ''}
          {!source.known ? ` · ${t('wiki.unknownSource')}` : ''}
        </li>
      ))}
    </ul>
  )
}

/** The page's blocks: each statement with its sources beside it. */
export function WikiBlocks({ blocks }: { blocks: WikiBlock[] }) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2">
      {blocks.map(block => (
        <div key={`${block.line}-${block.kind}`} className="grid gap-2 md:grid-cols-[1fr_auto] md:items-start">
          {block.kind === 'fact' || block.kind === 'code' || block.kind === 'row' || block.kind === 'table_header' ? (
            <pre className="whitespace-pre-wrap break-words rounded bg-surface-800 px-2 py-1 font-mono text-xs text-gray-200">{block.text}</pre>
          ) : (
            <WikiMarkdown text={block.text} />
          )}
          <div className="md:max-w-xs">
            {block.statement && !block.sources.length
              ? <span className="badge border border-amber-700/50 text-amber-300 text-[10px]">{t('wiki.noSource')}</span>
              : <Sources sources={block.sources} />}
          </div>
        </div>
      ))}
    </div>
  )
}

function VersionLine({ version }: { version: WikiVersion }) {
  const { t } = useTranslation()
  return (
    <li className="text-xs text-gray-400">
      <span className="font-semibold text-gray-200">{t('wiki.version', { number: version.number })}</span>{' · '}
      {t(`wiki.versionStatus.${version.status}`)}{' · '}
      {version.author_kind === 'person' ? t('wiki.byPerson', { who: version.author }) : t('wiki.byAgent', { who: version.author })}
      {version.sprint_id ? ` · ${t('wiki.sprint', { id: version.sprint_id })}` : ''}
      {version.decision_reason ? ` · ${version.decision_reason}` : ''}
    </li>
  )
}

export function WikiPageView({ page, companyId, canManage }: { page: WikiPageDetail; companyId: number; canManage: boolean }) {
  const { t } = useTranslation()
  const [editing, setEditing] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [title, setTitle] = useState(page.title)
  const [body, setBody] = useState(page.body)
  const [reason, setReason] = useState('')
  const write = useWriteWikiPage(companyId)
  const decide = useDecideWikiVersion(companyId)
  const history = useWikiVersions(companyId, page.slug, showHistory)
  const proposal = page.proposed_version

  return (
    <article aria-labelledby="wiki-page-title" className="card space-y-4 p-4">
      <header className="flex flex-wrap items-center gap-2">
        <h2 id="wiki-page-title" className="text-lg font-semibold text-white">{page.title}</h2>
        <span className={`badge border ${WIKI_STATUS_STYLES[page.status] ?? ''}`}>{t(`wiki.status.${page.status}`)}</span>
        <span className="badge">{t(`wiki.kinds.${page.kind}`)}</span>
        {page.version > 0 && <span className="text-xs text-gray-400">{t('wiki.version', { number: page.version })}</span>}
        {page.person_locked && <span className="text-xs text-gray-400">{t('wiki.personLocked')}</span>}
        {canManage && !editing && (
          <button type="button" className="btn btn-sm ml-auto" onClick={() => { setTitle(page.title); setBody(page.body); setEditing(true) }}>
            <Pencil size={13} aria-hidden="true" /> {t('wiki.edit')}
          </button>
        )}
      </header>
      <p className="text-xs text-gray-400">{t('wiki.claimNote')}</p>
      {page.module?.path && <p className="text-xs text-gray-400">{t('wiki.modulePath', { path: page.module.path })}</p>}

      {page.lint.length > 0 && (
        <section role="alert" aria-label={t('wiki.lintTitle')} className="alert-error text-sm">
          <p className="font-semibold">{t('wiki.lintTitle')}</p>
          <ul className="list-disc pl-5">
            {page.lint.map((reason, i) => <li key={i}>{t(`wiki.lintRule.${reason.rule}`)}: {reason.text}</li>)}
          </ul>
        </section>
      )}

      {editing ? (
        <form className="space-y-2" onSubmit={event => {
          event.preventDefault()
          write.mutate({ slug: page.slug, data: { kind: page.kind, title, body } }, { onSuccess: () => setEditing(false) })
        }}>
          <label className="block text-xs text-gray-400">{t('wiki.titleLabel')}
            <input className="input mt-1 w-full" value={title} onChange={e => setTitle(e.target.value)} maxLength={200} required />
          </label>
          <label className="block text-xs text-gray-400">{t('wiki.bodyLabel')}
            <textarea className="input mt-1 h-72 w-full font-mono text-xs" value={body} onChange={e => setBody(e.target.value)} maxLength={20000} />
          </label>
          <p className="text-xs text-gray-400">{t('wiki.syntaxHelp')}</p>
          {write.isError && <p role="alert" className="alert-error text-sm">{t('wiki.saveError', { message: errorDetail(write.error) })}</p>}
          <div className="flex gap-2">
            <button type="submit" className="btn btn-primary btn-sm" disabled={write.isPending}>
              {write.isPending && <Loader2 size={13} className="animate-spin" />} {t('wiki.save')}
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setEditing(false)}>{t('common.cancel', { defaultValue: 'Cancel' })}</button>
          </div>
        </form>
      ) : page.blocks.length ? (
        <WikiBlocks blocks={page.blocks} />
      ) : (
        <p className="text-sm text-gray-400">{t('wiki.noBody')}</p>
      )}

      {proposal && (
        <section aria-label={t('wiki.proposalTitle')} className="space-y-2 rounded border border-sky-700/40 p-3">
          <h3 className="text-sm font-semibold text-sky-300">{t('wiki.proposalTitle')}</h3>
          <p className="text-xs text-gray-400">{t('wiki.proposalNote', { who: proposal.author, number: proposal.number })}</p>
          {proposal.findings.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-amber-300">
              {proposal.findings.map(f => <li key={f.line}>{t('wiki.uncitedLine', { line: f.line, excerpt: f.excerpt })}</li>)}
            </ul>
          )}
          <WikiBlocks blocks={proposal.blocks ?? []} />
          {canManage && (
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className="btn btn-primary btn-sm" disabled={decide.isPending}
                onClick={() => decide.mutate({ id: proposal.id, accept: true })}>
                <Check size={13} aria-hidden="true" /> {t('wiki.accept')}
              </button>
              <input className="input input-sm" aria-label={t('wiki.rejectReason')} placeholder={t('wiki.rejectReason')}
                value={reason} onChange={e => setReason(e.target.value)} maxLength={500} />
              <button type="button" className="btn btn-sm" disabled={decide.isPending}
                onClick={() => decide.mutate({ id: proposal.id, accept: false, reason })}>
                <X size={13} aria-hidden="true" /> {t('wiki.reject')}
              </button>
            </div>
          )}
          {decide.isError && <p role="alert" className="alert-error text-sm">{t('wiki.saveError', { message: errorDetail(decide.error) })}</p>}
        </section>
      )}

      <section>
        <button type="button" className="btn btn-sm" aria-expanded={showHistory} onClick={() => setShowHistory(v => !v)}>
          <History size={13} aria-hidden="true" /> {t('wiki.history')}
        </button>
        {showHistory && (
          history.isLoading ? <p role="status" className="text-xs text-gray-400">{t('wiki.loading')}</p> : (
            <ul className="mt-2 space-y-1">
              {(history.data?.versions ?? []).map(v => <VersionLine key={v.id} version={v} />)}
            </ul>
          )
        )}
      </section>
    </article>
  )
}
