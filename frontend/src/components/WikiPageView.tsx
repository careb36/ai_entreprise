import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, CircleCheck, CircleDot, Clock, History, Pencil, TriangleAlert, X, type LucideIcon } from 'lucide-react'
import { useDecideWikiVersion, useWikiVersions, useWriteWikiPage } from '../hooks/useWiki'
import { errorDetail, type WikiBlock, type WikiPageDetail, type WikiSource, type WikiVersion } from '../services/api'
import { WikiMarkdown } from './WikiMarkdown'
import { Spinner, StatusBadge, type StatusTone } from './ui'

/** A page's status as an icon and a word (current, stale, proposed); never the colour alone. */
const WIKI_STATUS_LOOK: Record<string, { icon: LucideIcon; tone: StatusTone }> = {
  current: { icon: CircleCheck, tone: 'success' },
  stale: { icon: Clock, tone: 'warning' },
  proposed: { icon: CircleDot, tone: 'info' },
}

export function WikiStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  const look = WIKI_STATUS_LOOK[status] ?? { icon: CircleDot, tone: 'neutral' as const }
  return <StatusBadge icon={look.icon} tone={look.tone} label={t(`wiki.status.${status}`)} />
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
          className={`chip font-mono text-[10px] ${source.known ? '' : 'border-red-700/50 text-red-300'}`}
          title={source.title ?? undefined}>
          {!source.known && <TriangleAlert size={10} aria-hidden="true" className="mr-1" />}
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
    <div className="space-y-2.5">
      {blocks.map(block => (
        <div key={`${block.line}-${block.kind}`} className="grid gap-2 md:grid-cols-[minmax(0,1fr)_14rem] md:items-start md:gap-4">
          {block.kind === 'fact' || block.kind === 'code' || block.kind === 'row' || block.kind === 'table_header' ? (
            <pre className="max-w-prose whitespace-pre-wrap break-words rounded-md border border-surface-700 bg-surface-950/60 px-2.5 py-1.5 font-mono text-xs text-gray-200">{block.text}</pre>
          ) : (
            <WikiMarkdown text={block.text}
              className="max-w-prose leading-relaxed prose-headings:font-semibold prose-headings:text-gray-50 prose-p:text-gray-300 prose-li:text-gray-300 prose-a:text-brand-300" />
          )}
          <div>
            {block.statement && !block.sources.length
              ? <span className="chip border-dashed border-amber-700/60 text-[10px] text-amber-300">{t('wiki.noSource')}</span>
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
    <li className="data-row py-2 text-xs text-gray-400">
      <span className="font-semibold tabular-nums text-gray-200">{t('wiki.version', { number: version.number })}</span>{' · '}
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
    <article aria-labelledby="wiki-page-title" className="panel">
      <header className="panel-header gap-y-1.5 px-4 py-3">
        <h2 id="wiki-page-title" className="text-base font-semibold tracking-tight text-gray-50">{page.title}</h2>
        <WikiStatusBadge status={page.status} />
        <span className="chip">{t(`wiki.kinds.${page.kind}`)}</span>
        {page.version > 0 && <span className="text-meta tabular-nums">{t('wiki.version', { number: page.version })}</span>}
        {page.person_locked && <span className="text-meta">{t('wiki.personLocked')}</span>}
        {canManage && !editing && (
          <button type="button" className="btn-tool ml-auto" onClick={() => { setTitle(page.title); setBody(page.body); setEditing(true) }}>
            <Pencil size={13} aria-hidden="true" /> {t('wiki.edit')}
          </button>
        )}
      </header>

      <div className="space-y-4 px-4 py-4 sm:px-5">
        <div className="space-y-1">
          <p className="text-meta max-w-prose">{t('wiki.claimNote')}</p>
          {page.module?.path && <p className="text-meta font-mono">{t('wiki.modulePath', { path: page.module.path })}</p>}
        </div>

        {page.lint.length > 0 && (
          <section role="alert" aria-label={t('wiki.lintTitle')} className="alert-error flex-col gap-1">
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
            {write.isError && <p role="alert" className="alert-error">{t('wiki.saveError', { message: errorDetail(write.error) })}</p>}
            <div className="flex gap-2">
              <button type="submit" className="btn btn-primary btn-sm" disabled={write.isPending}>
                {write.isPending && <Spinner size={13} />} {t('wiki.save')}
              </button>
              <button type="button" className="btn-tool" onClick={() => setEditing(false)}>{t('common.cancel', { defaultValue: 'Cancel' })}</button>
            </div>
          </form>
        ) : page.blocks.length ? (
          <WikiBlocks blocks={page.blocks} />
        ) : (
          <p className="text-[13px] text-gray-400">{t('wiki.noBody')}</p>
        )}

        {proposal && (
          <section aria-label={t('wiki.proposalTitle')} className="space-y-2.5 rounded-lg border border-dashed border-sky-700/60 p-3">
            <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-sky-300">
              <CircleDot size={13} aria-hidden="true" />{t('wiki.proposalTitle')}
            </h3>
            <p className="text-xs text-gray-400">{t('wiki.proposalNote', { who: proposal.author, number: proposal.number })}</p>
            {proposal.findings.length > 0 && (
              <ul className="list-disc pl-5 text-xs text-amber-300">
                {proposal.findings.map(f => <li key={f.line}>{t('wiki.uncitedLine', { line: f.line, excerpt: f.excerpt })}</li>)}
              </ul>
            )}
            <WikiBlocks blocks={proposal.blocks ?? []} />
            {canManage && (
              <div className="flex flex-wrap items-center gap-2 border-t border-surface-700 pt-2.5">
                <button type="button" className="btn btn-primary btn-sm" disabled={decide.isPending}
                  onClick={() => decide.mutate({ id: proposal.id, accept: true })}>
                  <Check size={13} aria-hidden="true" /> {t('wiki.accept')}
                </button>
                <input className="input control-sm w-56" aria-label={t('wiki.rejectReason')} placeholder={t('wiki.rejectReason')}
                  value={reason} onChange={e => setReason(e.target.value)} maxLength={500} />
                <button type="button" className="btn-tool" disabled={decide.isPending}
                  onClick={() => decide.mutate({ id: proposal.id, accept: false, reason })}>
                  <X size={13} aria-hidden="true" /> {t('wiki.reject')}
                </button>
              </div>
            )}
            {decide.isError && <p role="alert" className="alert-error">{t('wiki.saveError', { message: errorDetail(decide.error) })}</p>}
          </section>
        )}

        <section className="space-y-2">
          <button type="button" className={`btn-tool ${showHistory ? 'btn-tool-on' : ''}`} aria-expanded={showHistory} onClick={() => setShowHistory(v => !v)}>
            <History size={13} aria-hidden="true" /> {t('wiki.history')}
          </button>
          {showHistory && (
            history.isLoading ? <p role="status" className="text-xs text-gray-400">{t('wiki.loading')}</p> : (
              <ul className="data-list rounded-md border border-surface-700">
                {(history.data?.versions ?? []).map(v => <VersionLine key={v.id} version={v} />)}
              </ul>
            )
          )}
        </section>
      </div>
    </article>
  )
}
