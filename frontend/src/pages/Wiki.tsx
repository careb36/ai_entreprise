import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BookOpen, Plus, ScanSearch } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { useLintWiki, useWiki, useWikiPage, useWikiSettings, useWriteWikiPage } from '../hooks/useWiki'
import { WIKI_KINDS, errorDetail, type WikiKind, type WikiPageSummary } from '../services/api'
import { useAppStore } from '../store/useAppStore'
import { WikiPageView, WikiStatusBadge } from '../components/WikiPageView'
import { CountPill, EmptyState, LoadError, LoadingLine, PageHeader, Spinner } from '../components/ui'

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
    <div className="page-container max-w-6xl space-y-4">
      <PageHeader title={t('wiki.title')} subtitle={t('wiki.subtitle')} />

      {query.isLoading ? (
        <LoadingLine>{t('wiki.loading')}</LoadingLine>
      ) : query.isError ? (
        <LoadError onRetry={() => query.refetch()}>{t('wiki.loadError', { message: errorDetail(query.error) })}</LoadError>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
          <aside className="space-y-3">
            <PageList pages={pages} selected={slug} onSelect={setSelected} />
            {canManage && companyId !== null && (
              <>
                <NewPage companyId={companyId} onCreated={setSelected} />
                <Settings companyId={companyId} writeToRepo={query.data?.settings.write_to_repo ?? false} />
              </>
            )}
          </aside>
          <div className="min-w-0">
            {!pages.length ? (
              <EmptyState icon={BookOpen} title={t('wiki.empty')} dashed />
            ) : page.isLoading ? (
              <LoadingLine>{t('wiki.loading')}</LoadingLine>
            ) : page.isError ? (
              <p role="alert" className="alert-error">{t('wiki.loadError', { message: errorDetail(page.error) })}</p>
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
  if (!pages.length) return null
  return (
    <nav aria-label={t('wiki.pages')} className="panel space-y-3 p-2">
      {WIKI_KINDS.map(kind => {
        const ofKind = pages.filter(p => p.kind === kind)
        if (!ofKind.length) return null
        return (
          <div key={kind}>
            <h2 className="flex items-center justify-between px-2 pb-1 text-[11px] font-semibold uppercase tracking-wider text-gray-400">
              {t(`wiki.kindsPlural.${kind}`)}
              <CountPill count={ofKind.length} label={t('wiki.countLabel', { count: ofKind.length })} />
            </h2>
            <ul className="space-y-0.5">
              {ofKind.map(p => (
                <li key={p.slug}>
                  <button type="button" aria-current={p.slug === selected ? 'page' : undefined} onClick={() => onSelect(p.slug)}
                    className={`focus-ring flex w-full flex-col items-start gap-1 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors hover:bg-surface-800 motion-reduce:transition-none ${
                      p.slug === selected ? 'bg-surface-800 text-white ring-1 ring-inset ring-surface-700' : 'text-gray-300'}`}>
                    <span className="w-full truncate">{p.title}</span>
                    <WikiStatusBadge status={p.status} />
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
      <button type="button" className="btn-tool w-full justify-center border border-dashed border-surface-700" onClick={() => setOpen(true)}>
        <Plus size={13} aria-hidden="true" /> {t('wiki.newPage')}
      </button>
    )
  }
  return (
    <form aria-label={t('wiki.newPage')} className="panel space-y-2 p-3" onSubmit={event => {
      event.preventDefault()
      write.mutate({ slug, data: { kind, title, body: '' } }, { onSuccess: () => { setOpen(false); onCreated(slug) } })
    }}>
      <label className="block text-xs text-gray-400">{t('wiki.slugLabel')}
        <input className="input control-sm mt-1 w-full font-mono" value={slug} onChange={e => setSlug(e.target.value.toLowerCase())} required pattern={SLUG.source} />
      </label>
      <label className="block text-xs text-gray-400">{t('wiki.kindLabel')}
        <select className="select control-sm mt-1 w-full" value={kind} onChange={e => setKind(e.target.value as WikiKind)}>
          {WIKI_KINDS.map(k => <option key={k} value={k}>{t(`wiki.kinds.${k}`)}</option>)}
        </select>
      </label>
      <label className="block text-xs text-gray-400">{t('wiki.titleLabel')}
        <input className="input control-sm mt-1 w-full" value={title} onChange={e => setTitle(e.target.value)} required maxLength={200} />
      </label>
      {write.isError && <p role="alert" className="alert-error text-xs">{t('wiki.saveError', { message: errorDetail(write.error) })}</p>}
      <div className="flex gap-2">
        <button type="submit" className="btn btn-primary btn-sm" disabled={write.isPending || !SLUG.test(slug)}>{t('wiki.create')}</button>
        <button type="button" className="btn-tool" onClick={() => setOpen(false)}>{t('common.cancel', { defaultValue: 'Cancel' })}</button>
      </div>
    </form>
  )
}

function Settings({ companyId, writeToRepo }: { companyId: number; writeToRepo: boolean }) {
  const { t } = useTranslation()
  const settings = useWikiSettings(companyId)
  const lint = useLintWiki(companyId)
  return (
    <section aria-label={t('wiki.settings')} className="panel space-y-2 p-3 text-xs">
      <label className="flex items-start gap-2 text-gray-300">
        <input type="checkbox" className="mt-0.5" checked={writeToRepo} disabled={settings.isPending}
          onChange={e => settings.mutate(e.target.checked)} />
        <span>{t('wiki.writeToRepo')}</span>
      </label>
      <button type="button" className="btn-tool w-full justify-center" disabled={lint.isPending} onClick={() => lint.mutate()}>
        {lint.isPending ? <Spinner size={13} /> : <ScanSearch size={13} aria-hidden="true" />} {t('wiki.runLint')}
      </button>
      {lint.isSuccess && (
        <p role="status" className="text-xs text-gray-400">{t('wiki.lintDone', { count: Object.keys(lint.data.stale).length })}</p>
      )}
    </section>
  )
}
