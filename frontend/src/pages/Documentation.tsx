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
import { AlertTriangle, BookOpen, CheckCircle, FolderOutput, Loader2, RefreshCw } from 'lucide-react'
import { C4DiagramView } from '../components/C4DiagramView'
import { PackMarkdown } from '../components/PackMarkdown'
import { useCurrentRole } from '../hooks/useQueries'
import { errorDetail } from '../services/api'
import {
  DOC_LANGUAGES, type DocLanguage, type DocumentationPack, type LintFinding, PackConflict,
  useApprovePack, useCompilePack, useDocLanguage, usePack, usePacks, useSetDocLanguage, useWritePack,
} from '../services/documentation'
import { useAppStore } from '../store/useAppStore'

const DIAGRAMS_TAB = '__diagrams'

function Findings({ findings }: { findings: LintFinding[] }) {
  const { t } = useTranslation()
  if (findings.length === 0) return null
  return (
    <ul className="space-y-1 text-sm">
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
    <section aria-labelledby="pack-lint" className="card p-4 space-y-2">
      <h2 id="pack-lint" className="text-sm font-semibold text-white">{t('documentation.lint.title')}</h2>
      {lint.ok ? (
        <p className="flex items-center gap-2 text-sm text-emerald-300"><CheckCircle size={15} aria-hidden="true" /> {t('documentation.lint.ok')}</p>
      ) : (
        <p className="flex items-center gap-2 text-sm text-red-300"><AlertTriangle size={15} aria-hidden="true" /> {t('documentation.lint.stale', { count: lint.blocking })}</p>
      )}
      <Findings findings={shown} />
      {gaps > 0 && <p className="text-xs text-gray-400">{t('documentation.lint.gaps', { count: gaps })}</p>}
    </section>
  )
}

function PackView({ pack, canManage, isOwner }: { pack: DocumentationPack; canManage: boolean; isOwner: boolean }) {
  const { t } = useTranslation()
  const [tab, setTab] = useState(pack.documents[0]?.key ?? DIAGRAMS_TAB)
  const approve = useApprovePack()
  const write = useWritePack()
  const doc = pack.documents.find(d => d.key === tab)
  const approveError = approve.error instanceof PackConflict ? approve.error.findings : []
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-sm text-gray-300">
        <span>{t('documentation.version', { version: pack.version })}</span>
        <span className="text-xs text-gray-400">{t(`documentation.languages.${pack.language ?? 'en'}`)}</span>
        <span className="rounded-full bg-surface-800 px-2 py-0.5 text-xs">{t(`documentation.status.${pack.status}`)}</span>
        <span className="font-mono text-xs text-gray-500" title={t('documentation.inputsHash')}>{pack.inputs_sha256.slice(0, 16)}</span>
        {isOwner && pack.status === 'draft' && (
          <button type="button" className="btn-primary" disabled={approve.isPending || !pack.lint.ok} onClick={() => approve.mutate(pack.id)}>
            {approve.isPending ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle size={14} />} {t('documentation.approve')}
          </button>
        )}
        {canManage && pack.status === 'approved' && (
          <button type="button" className="btn-secondary" disabled={write.isPending} onClick={() => write.mutate(pack.id)}>
            {write.isPending ? <Loader2 size={14} className="animate-spin" /> : <FolderOutput size={14} />} {t('documentation.write')}
          </button>
        )}
      </div>
      {approve.isError && (
        <div role="alert" className="alert-error text-sm space-y-1">
          <p>{t('documentation.approveError', { message: errorDetail(approve.error) })}</p>
          <Findings findings={approveError} />
        </div>
      )}
      {write.isError && <div role="alert" className="alert-error text-sm">{t('documentation.writeError', { message: errorDetail(write.error) })}</div>}
      {pack.written_files.length > 0 && (
        <p className="text-xs text-gray-400">{t('documentation.written', { count: pack.written_files.length, path: 'docs/project/' })}</p>
      )}
      <LintPanel pack={pack} />
      <div role="tablist" aria-label={t('documentation.documents')} className="flex flex-wrap gap-2">
        {pack.documents.map(d => (
          <button key={d.key} role="tab" type="button" aria-selected={tab === d.key} onClick={() => setTab(d.key)}
            className={`rounded-lg px-3 py-1 text-sm ${tab === d.key ? 'bg-brand-600 text-white' : 'bg-surface-800 text-gray-300'}`}>
            {d.title}
          </button>
        ))}
        <button role="tab" type="button" aria-selected={tab === DIAGRAMS_TAB} onClick={() => setTab(DIAGRAMS_TAB)}
          className={`rounded-lg px-3 py-1 text-sm ${tab === DIAGRAMS_TAB ? 'bg-brand-600 text-white' : 'bg-surface-800 text-gray-300'}`}>
          {t('documentation.diagrams')}
        </button>
      </div>
      {tab === DIAGRAMS_TAB ? (
        pack.diagrams.length === 0
          ? <p className="text-sm text-gray-400">{t('documentation.noDiagrams')}</p>
          : <div className="space-y-4">{pack.diagrams.map(d => <C4DiagramView key={d.key} diagram={d} />)}</div>
      ) : doc ? (
        <article className="card p-4 space-y-2" role="tabpanel">
          <p className="text-xs text-gray-400">
            {t('documentation.file', { file: doc.file })} · {t('documentation.sourcesCount', { count: doc.sources.length })}
            {doc.claims.length > 0 && <> · {t('documentation.claimsCount', { count: doc.claims.length })}</>}
            {doc.persons.length > 0 && <> · {t('documentation.personsCount', { count: doc.persons.length })}</>}
            {doc.domain && <> · {t('documentation.placement', { domain: t(`documentation.domains.${doc.domain}`), focus: t(`documentation.focusAreas.${doc.focus_area}`) })}</>}
          </p>
          <PackMarkdown markdown={doc.markdown} />
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
    <div className="page-container max-w-5xl space-y-6">
      <div className="page-header">
        <div>
          <div className="flex items-center gap-2">
            <BookOpen className="text-brand-400" size={22} aria-hidden="true" />
            <h1 className="page-title">{t('documentation.title')}</h1>
          </div>
          <p className="text-gray-400 mt-1">{t('documentation.subtitle')}</p>
        </div>
      </div>

      {canManage && companyId !== null && (
        <section className="card p-4 flex flex-wrap items-center gap-3">
          <button type="button" className="btn-primary" disabled={compile.isPending}
            onClick={() => compile.mutate({ companyId, prose }, { onSuccess: created => setChosen(created.id) })}>
            {compile.isPending ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} {t('documentation.compile')}
          </button>
          <label className="flex items-center gap-2 text-sm text-gray-300">
            <input type="checkbox" checked={prose} onChange={e => setProse(e.target.checked)} /> {t('documentation.prose')}
          </label>
          <label className="flex items-center gap-2 text-sm text-gray-300">
            {t('documentation.language')}
            <select className="input max-w-[10rem]" value={language.data?.doc_language ?? 'en'} disabled={setLanguage.isPending || !language.data}
              onChange={e => setLanguage.mutate({ companyId, docLanguage: e.target.value as DocLanguage })}>
              {DOC_LANGUAGES.map(code => <option key={code} value={code}>{t(`documentation.languages.${code}`)}</option>)}
            </select>
          </label>
          {compile.isError && <span role="alert" className="text-sm text-red-300">{t('documentation.compileError', { message: errorDetail(compile.error) })}</span>}
          {setLanguage.isError && <span role="alert" className="text-sm text-red-300">{t('documentation.languageError', { message: errorDetail(setLanguage.error) })}</span>}
        </section>
      )}

      {packs.isLoading ? (
        <div role="status" className="flex items-center gap-2 text-sm text-gray-400"><Loader2 size={15} className="animate-spin" /> {t('documentation.loading')}</div>
      ) : packs.isError ? (
        <div role="alert" className="alert-error text-sm">{t('documentation.loadError', { message: errorDetail(packs.error) })}</div>
      ) : list.length === 0 ? (
        <p className="text-sm text-gray-400">{t('documentation.empty')}</p>
      ) : (
        <>
          <label className="flex items-center gap-2 text-sm text-gray-300">
            {t('documentation.versions')}
            <select className="input max-w-xs" value={selected ?? ''} onChange={e => setChosen(Number(e.target.value))}>
              {list.map(p => (
                <option key={p.id} value={p.id}>{t('documentation.versionOption', { version: p.version, status: t(`documentation.status.${p.status}`) })}</option>
              ))}
            </select>
          </label>
          {pack.isLoading && <div role="status" className="text-sm text-gray-400"><Loader2 size={15} className="inline animate-spin" /> {t('documentation.loading')}</div>}
          {pack.isError && <div role="alert" className="alert-error text-sm">{t('documentation.loadError', { message: errorDetail(pack.error) })}</div>}
          {pack.data && <PackView key={pack.data.id} pack={pack.data} canManage={canManage} isOwner={role === 'Owner'} />}
        </>
      )}
    </div>
  )
}
