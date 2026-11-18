/**
 * The project's management documentation (issue #530): the documentation packs
 * La Fabrique compiles from its records, one version per compile. A Manager
 * compiles a new draft (optionally with the TechWriter's background for the
 * charter, marked as a claim) and writes an approved pack to `docs/project/`;
 * an Owner approves a draft while the lint finds no drift from the records.
 * Each document renders as safe Markdown, the C4 diagrams as SVG.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Archive, BookOpen, CheckCircle, CircleCheck, FolderOutput, PencilLine, RefreshCw, type LucideIcon } from 'lucide-react'
import { C4DiagramView } from '../components/C4DiagramView'
import { PackMarkdown } from '../components/PackMarkdown'
import { LoadErrorAlert, LoadingLine, Spinner } from '../components/knowledge/PageStates'
import { EmptyState, PageHeader, Panel, StatusBadge, Toolbar, type StatusTone } from '../components/ui'
import { useCurrentRole } from '../hooks/useQueries'
import { errorDetail } from '../services/api'
import {
  DOC_LANGUAGES, type DocLanguage, type DocumentationPack, type LintFinding, PackConflict,
  useApprovePack, useCompilePack, useDocLanguage, usePack, usePacks, useSetDocLanguage, useWritePack,
} from '../services/documentation'
import { useAppStore } from '../store/useAppStore'

const DIAGRAMS_TAB = '__diagrams'

const PACK_STATUS_LOOK: Record<string, { icon: LucideIcon; tone: StatusTone }> = {
  draft: { icon: PencilLine, tone: 'warning' },
  approved: { icon: CircleCheck, tone: 'success' },
  superseded: { icon: Archive, tone: 'neutral' },
}

function tabClass(selected: boolean) {
  return `segmented-item ${selected ? 'bg-surface-700 text-white shadow-sm hover:bg-surface-700' : ''}`
}

function Findings({ findings }: { findings: LintFinding[] }) {
  const { t } = useTranslation()
  if (findings.length === 0) return null
  return (
    <ul className="space-y-1 text-[13px]">
      {findings.map((f, i) => (
        <li key={`${f.where}-${i}`} className={f.severity === 'blocking' ? 'text-red-300' : f.severity === 'warning' ? 'text-amber-300' : 'text-gray-400'}>
          <span className="font-mono text-xs">{f.where}</span> · {t(`documentation.severity.${f.severity}`)}: {f.message}
        </li>
      ))}
    </ul>
  )
}

function LintPanel({ pack }: { pack: DocumentationPack }) {
  const { t } = useTranslation()
  const { lint } = pack
  const shown = lint.findings.filter(f => f.severity !== 'info')
  const gaps = lint.findings.length - shown.length
  return (
    <Panel title={t('documentation.lint.title')}>
      <div className="space-y-2 p-3">
        {lint.ok ? (
          <p className="flex items-center gap-2 text-[13px] text-emerald-300"><CheckCircle size={14} aria-hidden="true" /> {t('documentation.lint.ok')}</p>
        ) : (
          <p className="flex items-center gap-2 text-[13px] text-red-300"><AlertTriangle size={14} aria-hidden="true" /> {t('documentation.lint.stale', { count: lint.blocking })}</p>
        )}
        <Findings findings={shown} />
        {gaps > 0 && <p className="text-xs text-gray-400">{t('documentation.lint.gaps', { count: gaps })}</p>}
      </div>
    </Panel>
  )
}

function PackView({ pack, canManage, isOwner }: { pack: DocumentationPack; canManage: boolean; isOwner: boolean }) {
  const { t } = useTranslation()
  const [tab, setTab] = useState(pack.documents[0]?.key ?? DIAGRAMS_TAB)
  const approve = useApprovePack()
  const write = useWritePack()
  const doc = pack.documents.find(d => d.key === tab)
  const approveError = approve.error instanceof PackConflict ? approve.error.findings : []
  const look = PACK_STATUS_LOOK[pack.status] ?? PACK_STATUS_LOOK.draft
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-gray-300">
        <span className="tabular-nums">{t('documentation.version', { version: pack.version })}</span>
        <span className="chip">{t(`documentation.languages.${pack.language ?? 'en'}`)}</span>
        <StatusBadge icon={look.icon} tone={look.tone} label={t(`documentation.status.${pack.status}`)} />
        <span className="font-mono text-[11px] text-gray-400" title={t('documentation.inputsHash')}>{pack.inputs_sha256.slice(0, 16)}</span>
        {isOwner && pack.status === 'draft' && (
          <button type="button" className="btn btn-primary btn-sm" disabled={approve.isPending || !pack.lint.ok} onClick={() => approve.mutate(pack.id)}>
            {approve.isPending ? <Spinner /> : <CheckCircle size={14} aria-hidden="true" />} {t('documentation.approve')}
          </button>
        )}
        {canManage && pack.status === 'approved' && (
          <button type="button" className="btn-tool" disabled={write.isPending} onClick={() => write.mutate(pack.id)}>
            {write.isPending ? <Spinner size={13} /> : <FolderOutput size={13} aria-hidden="true" />} {t('documentation.write')}
          </button>
        )}
      </div>
      {approve.isError && (
        <div role="alert" className="alert-error flex-col gap-1">
          <p>{t('documentation.approveError', { message: errorDetail(approve.error) })}</p>
          <Findings findings={approveError} />
        </div>
      )}
      {write.isError && <div role="alert" className="alert-error">{t('documentation.writeError', { message: errorDetail(write.error) })}</div>}
      {pack.written_files.length > 0 && (
        <p className="flex items-center gap-1.5 text-xs text-gray-400">
          <FolderOutput size={12} aria-hidden="true" />{t('documentation.written', { count: pack.written_files.length, path: 'docs/project/' })}
        </p>
      )}
      <LintPanel pack={pack} />
      <div role="tablist" aria-label={t('documentation.documents')} className="segmented flex-wrap">
        {pack.documents.map(d => (
          <button key={d.key} role="tab" type="button" aria-selected={tab === d.key} onClick={() => setTab(d.key)} className={tabClass(tab === d.key)}>
            {d.title}
          </button>
        ))}
        <button role="tab" type="button" aria-selected={tab === DIAGRAMS_TAB} onClick={() => setTab(DIAGRAMS_TAB)} className={tabClass(tab === DIAGRAMS_TAB)}>
          {t('documentation.diagrams')}
        </button>
      </div>
      {tab === DIAGRAMS_TAB ? (
        pack.diagrams.length === 0
          ? <EmptyState title={t('documentation.noDiagrams')} dashed />
          : <div className="space-y-4">{pack.diagrams.map(d => <C4DiagramView key={d.key} diagram={d} />)}</div>
      ) : doc ? (
        <article className="panel" role="tabpanel">
          <p className="text-meta border-b border-surface-700 px-4 py-2 tabular-nums">
            {t('documentation.file', { file: doc.file })} · {t('documentation.sourcesCount', { count: doc.sources.length })}
            {doc.claims.length > 0 && <> · {t('documentation.claimsCount', { count: doc.claims.length })}</>}
            {doc.persons.length > 0 && <> · {t('documentation.personsCount', { count: doc.persons.length })}</>}
            {doc.domain && <> · {t('documentation.placement', { domain: t(`documentation.domains.${doc.domain}`), focus: t(`documentation.focusAreas.${doc.focus_area}`) })}</>}
          </p>
          <div className="px-4 py-5 sm:px-6">
            <PackMarkdown markdown={doc.markdown} />
          </div>
        </article>
      ) : null}
    </div>
  )
}

export default function Documentation() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'
  const packs = usePacks(companyId)
  const [chosen, setChosen] = useState<number | null>(null)
  const [prose, setProse] = useState(true)
  const compile = useCompilePack()
  const language = useDocLanguage(companyId)
  const setLanguage = useSetDocLanguage()
  const list = packs.data?.packs ?? []
  const selected = chosen ?? list[0]?.id ?? null
  const pack = usePack(selected)

  return (
    <div className="page-container max-w-5xl space-y-4">
      <PageHeader title={t('documentation.title')} subtitle={t('documentation.subtitle')} />

      {canManage && companyId !== null && (
        <div className="space-y-2">
          <Toolbar label={t('documentation.compileOptions')}>
            <button type="button" className="btn btn-secondary btn-sm" disabled={compile.isPending}
              onClick={() => compile.mutate({ companyId, prose }, { onSuccess: created => setChosen(created.id) })}>
              {compile.isPending ? <Spinner /> : <RefreshCw size={14} aria-hidden="true" />} {t('documentation.compile')}
            </button>
            <label className="flex items-center gap-2 text-xs text-gray-300">
              <input type="checkbox" checked={prose} onChange={e => setProse(e.target.checked)} /> {t('documentation.prose')}
            </label>
            <label className="flex items-center gap-2 text-xs text-gray-400">
              {t('documentation.language')}
              <select className="select control-sm w-auto pr-7" value={language.data?.doc_language ?? 'en'} disabled={setLanguage.isPending || !language.data}
                onChange={e => setLanguage.mutate({ companyId, docLanguage: e.target.value as DocLanguage })}>
                {DOC_LANGUAGES.map(code => <option key={code} value={code}>{t(`documentation.languages.${code}`)}</option>)}
              </select>
            </label>
          </Toolbar>
          {compile.isError && <p role="alert" className="text-[13px] text-red-300">{t('documentation.compileError', { message: errorDetail(compile.error) })}</p>}
          {setLanguage.isError && <p role="alert" className="text-[13px] text-red-300">{t('documentation.languageError', { message: errorDetail(setLanguage.error) })}</p>}
        </div>
      )}

      {packs.isLoading ? (
        <LoadingLine>{t('documentation.loading')}</LoadingLine>
      ) : packs.isError ? (
        <LoadErrorAlert>{t('documentation.loadError', { message: errorDetail(packs.error) })}</LoadErrorAlert>
      ) : list.length === 0 ? (
        <EmptyState icon={BookOpen} title={t('documentation.empty')} dashed />
      ) : (
        <>
          <Toolbar label={t('documentation.versions')}>
            <label className="flex items-center gap-2 text-xs text-gray-400">
              {t('documentation.versions')}
              <select className="select control-sm w-auto max-w-xs pr-7" value={selected ?? ''} onChange={e => setChosen(Number(e.target.value))}>
                {list.map(p => (
                  <option key={p.id} value={p.id}>{t('documentation.versionOption', { version: p.version, status: t(`documentation.status.${p.status}`) })}</option>
                ))}
              </select>
            </label>
          </Toolbar>
          {pack.isLoading && <LoadingLine>{t('documentation.loading')}</LoadingLine>}
          {pack.isError && <LoadErrorAlert>{t('documentation.loadError', { message: errorDetail(pack.error) })}</LoadErrorAlert>}
          {pack.data && <PackView key={pack.data.id} pack={pack.data} canManage={canManage} isOwner={role === 'Owner'} />}
        </>
      )}
    </div>
  )
}
