import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BookOpen, Loader2, Plus } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { useLintWiki, useWiki, useWikiPage, useWikiSettings, useWriteWikiPage } from '../hooks/useWiki'
import { WIKI_KINDS, errorDetail, type WikiKind, type WikiPageSummary } from '../services/api'
import { useAppStore } from '../store/useAppStore'
import { WIKI_STATUS_STYLES, WikiPageView } from '../components/WikiPageView'

const SLUG = /^[a-z0-9][a-z0-9-]{0,79}$/

/**
 * The project wiki: what the project knows, compiled by the agents from the
 * verified runs, the retrospectives and the sprint board, each statement with
 * its sources. A stale page is left out of prompts until it is revised; a
 * person's edit wins over the next compilation, which only proposes.
 */
export default function Wiki() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'
  const query = useWiki(companyId)
  const [selected, setSelected] = useState<string | null>(null)
  const pages = query.data?.pages ?? []
  const slug = selected ?? pages[0]?.slug ?? null
  const page = useWikiPage(companyId, slug)

  return (
    <div className="page-container max-w-6xl space-y-6">
      <div className="page-header">
        <div>
          <div className="flex items-center gap-2">
            <BookOpen className="text-brand-400" size={22} />
            <h1 className="page-title">{t('wiki.title')}</h1>
          </div>
          <p className="text-gray-400 mt-1">{t('wiki.subtitle')}</p>
        </div>
      </div>

      {query.isLoading ? (
        <div role="status" className="flex items-center gap-2 text-sm text-gray-400">
          <Loader2 size={15} className="animate-spin" /> {t('wiki.loading')}
        </div>
      ) : query.isError ? (
        <div role="alert" className="alert-error flex flex-wrap items-center gap-2 text-sm">
          <span>{t('wiki.loadError', { message: errorDetail(query.error) })}</span>
          <button type="button" onClick={() => query.refetch()} className="underline">{t('common.retry')}</button>
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
          <aside className="space-y-4">
            <PageList pages={pages} selected={slug} onSelect={setSelected} />
            {canManage && companyId !== null && (
              <>
                <NewPage companyId={companyId} onCreated={setSelected} />
                <Settings companyId={companyId} writeToRepo={query.data?.settings.write_to_repo ?? false} />
              </>
            )}
          </aside>
          <div>
            {!pages.length ? (
              <p className="card p-4 text-sm text-gray-400">{t('wiki.empty')}</p>
            ) : page.isLoading ? (
              <div role="status" className="text-sm text-gray-400">{t('wiki.loading')}</div>
            ) : page.isError ? (
              <p role="alert" className="alert-error text-sm">{t('wiki.loadError', { message: errorDetail(page.error) })}</p>
            ) : page.data && companyId !== null ? (
              <WikiPageView key={`${page.data.slug}-${page.data.version}`} page={page.data} companyId={companyId} canManage={canManage} />
            ) : null}
          </div>
        </div>
      )}
    </div>
  )
}

function PageList({ pages, selected, onSelect }: { pages: WikiPageSummary[]; selected: string | null; onSelect: (slug: string) => void }) {
  const { t } = useTranslation()
  return (
    <nav aria-label={t('wiki.pages')} className="card space-y-3 p-3">
      {WIKI_KINDS.map(kind => {
        const ofKind = pages.filter(p => p.kind === kind)
        if (!ofKind.length) return null
        return (
          <div key={kind}>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">{t(`wiki.kindsPlural.${kind}`)}</h2>
            <ul className="mt-1 space-y-1">
              {ofKind.map(p => (
                <li key={p.slug}>
                  <button type="button" aria-current={p.slug === selected ? 'page' : undefined} onClick={() => onSelect(p.slug)}
                    className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left text-sm hover:bg-surface-800 ${p.slug === selected ? 'bg-surface-800 text-white' : 'text-gray-300'}`}>
                    <span className="truncate">{p.title}</span>
                    <span className={`badge border text-[10px] ${WIKI_STATUS_STYLES[p.status] ?? ''}`}>{t(`wiki.status.${p.status}`)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )
      })}
    </nav>
  )
}

function NewPage({ companyId, onCreated }: { companyId: number; onCreated: (slug: string) => void }) {
  const { t } = useTranslation()
  const write = useWriteWikiPage(companyId)
  const [open, setOpen] = useState(false)
  const [slug, setSlug] = useState('')
  const [kind, setKind] = useState<WikiKind>('concept')
  const [title, setTitle] = useState('')
  if (!open) {
    return (
      <button type="button" className="btn btn-sm w-full" onClick={() => setOpen(true)}>
        <Plus size={13} aria-hidden="true" /> {t('wiki.newPage')}
      </button>
    )
  }
  return (
    <form aria-label={t('wiki.newPage')} className="card space-y-2 p-3" onSubmit={event => {
      event.preventDefault()
      write.mutate({ slug, data: { kind, title, body: '' } }, { onSuccess: () => { setOpen(false); onCreated(slug) } })
    }}>
      <label className="block text-xs text-gray-400">{t('wiki.slugLabel')}
        <input className="input mt-1 w-full" value={slug} onChange={e => setSlug(e.target.value.toLowerCase())} required pattern={SLUG.source} />
      </label>
      <label className="block text-xs text-gray-400">{t('wiki.kindLabel')}
        <select className="input mt-1 w-full" value={kind} onChange={e => setKind(e.target.value as WikiKind)}>
          {WIKI_KINDS.map(k => <option key={k} value={k}>{t(`wiki.kinds.${k}`)}</option>)}
        </select>
      </label>
      <label className="block text-xs text-gray-400">{t('wiki.titleLabel')}
        <input className="input mt-1 w-full" value={title} onChange={e => setTitle(e.target.value)} required maxLength={200} />
      </label>
      {write.isError && <p role="alert" className="alert-error text-xs">{t('wiki.saveError', { message: errorDetail(write.error) })}</p>}
      <div className="flex gap-2">
        <button type="submit" className="btn btn-primary btn-sm" disabled={write.isPending || !SLUG.test(slug)}>{t('wiki.create')}</button>
        <button type="button" className="btn btn-sm" onClick={() => setOpen(false)}>{t('common.cancel', { defaultValue: 'Cancel' })}</button>
      </div>
    </form>
  )
}

function Settings({ companyId, writeToRepo }: { companyId: number; writeToRepo: boolean }) {
  const { t } = useTranslation()
  const settings = useWikiSettings(companyId)
  const lint = useLintWiki(companyId)
  return (
    <section aria-label={t('wiki.settings')} className="card space-y-2 p-3 text-sm">
      <label className="flex items-start gap-2 text-gray-300">
        <input type="checkbox" checked={writeToRepo} disabled={settings.isPending}
          onChange={e => settings.mutate(e.target.checked)} />
        <span>{t('wiki.writeToRepo')}</span>
      </label>
      <button type="button" className="btn btn-sm w-full" disabled={lint.isPending} onClick={() => lint.mutate()}>
        {lint.isPending && <Loader2 size={13} className="animate-spin" />} {t('wiki.runLint')}
      </button>
      {lint.isSuccess && (
        <p role="status" className="text-xs text-gray-400">{t('wiki.lintDone', { count: Object.keys(lint.data.stale).length })}</p>
      )}
    </section>
  )
}
