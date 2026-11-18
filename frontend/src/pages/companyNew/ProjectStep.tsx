import { useTranslation } from 'react-i18next'
import { CircleCheck, Code2, Database, FolderOpen, FolderSearch, Gauge, ListChecks, Puzzle, Search, Server, Sparkles } from 'lucide-react'
import type { DetectedStack } from '../../services/api'
import { EmptyState, Spinner, StatusBadge, type StatusTone } from '../../components/ui'
import { FolderPicker } from '../../components/FolderPicker'
import { WorkspaceDaemonHelp } from '../../components/WorkspaceDaemonHelp'
import { DaemonStatusLine } from './DaemonStatusLine'
import { ProjectCard } from './ProjectCard'
import type { ProjectFolder } from './useProjectFolder'

const COMPLEXITY_TONE: Record<string, StatusTone> = { High: 'danger', Medium: 'warning', Low: 'success' }

/** Step 0: the project folder, the daemon's view of it, and the stack the scan detected. */
export function ProjectStep({ folder, discoveryResult, discoverPending, handleDiscover, mutationError }: {
  folder: ProjectFolder
  discoveryResult: DetectedStack | null
  discoverPending: boolean
  handleDiscover: () => void
  mutationError: string | null
}) {
  const { t } = useTranslation()
  const {
    projectPath, handlePathChange, inspected, inspectPath, hostPlatform, browseButtonRef, handleBrowse,
    selectDirectory, setPickerOpen, workspaceStatus, pickerOpen, workspaceHost, chooseFolder, closePicker,
    inspecting, inspectError, folderMissing,
  } = folder
  return (
  <div className="space-y-5">
    <div>
      <h2 className="mb-1 text-base font-semibold text-gray-50">{t('companyNew.scanTitle')}</h2>
      <p className="text-[13px] text-gray-400">
        {t('companyNew.scanDescription')}
      </p>
    </div>

    <div className="space-y-2">
      <label htmlFor="project-path" className="input-label">{t('projectFolder.label')}</label>
      <div className="flex flex-col sm:flex-row gap-2">
        <input id="project-path" type="text" value={projectPath}
          onChange={e => handlePathChange(e.target.value)}
          onBlur={() => {
            const typed = projectPath.trim()
            if (typed && inspected?.path !== typed && !discoverPending) void inspectPath(typed)
          }}
          onKeyDown={e => e.key === 'Enter' && handleDiscover()}
          placeholder={t(hostPlatform === 'win32' ? 'projectFolder.placeholderWindows' : 'projectFolder.placeholderUnix')}
          spellCheck={false} autoComplete="off"
          className="input flex-1 min-w-0 font-mono text-sm" />
        <button ref={browseButtonRef} type="button" onClick={handleBrowse}
          className="btn-secondary flex-shrink-0">
          <FolderOpen size={14} aria-hidden="true" />{t('companyNew.browse')}
        </button>
      </div>
      {selectDirectory && (
        <button type="button" onClick={() => setPickerOpen(true)}
          className="rounded-sm text-xs text-brand-300 hover:text-brand-200 hover:underline">
          {t('projectFolder.browseDaemon')}
        </button>
      )}
      <DaemonStatusLine
        host={workspaceStatus.data?.host}
        connected={workspaceStatus.data?.connected}
        state={workspaceStatus.data?.state}
        reason={workspaceStatus.data?.reason}
        isPending={workspaceStatus.isPending}
        error={workspaceStatus.error}
        fallbackPlatform={hostPlatform} />
    </div>

    {pickerOpen && (
      <FolderPicker
        initialPath={inspected?.exists && inspected.is_dir ? inspected.path : undefined}
        platform={workspaceHost?.platform}
        onSelect={chooseFolder}
        onClose={closePicker} />
    )}

    {inspecting && (
      <p role="status" className="flex items-center gap-2 text-xs text-gray-400">
        <Spinner size={13} />
        {t('projectFolder.checking')}
      </p>
    )}
    {!inspecting && inspected && <ProjectCard project={inspected} />}
    {!inspecting && inspectError?.status === 503 && (
      <WorkspaceDaemonHelp platform={hostPlatform} detail={inspectError.message}>
        <p className="text-yellow-300/80">{t('projectFolder.manualHint')}</p>
      </WorkspaceDaemonHelp>
    )}
    {!inspecting && inspectError && inspectError.status !== 503 && (
      <div role="alert" className="alert-error text-sm">
        <span className="break-all">{t('projectFolder.inspectError', { message: inspectError.message })}</span>
      </div>
    )}

    <div className="flex justify-end">
      <button type="button" onClick={handleDiscover}
        disabled={discoverPending || !projectPath.trim() || folderMissing}
        className="btn-primary min-w-28 flex-shrink-0">
        {discoverPending ? <Spinner size={13} /> : <Search size={14} aria-hidden="true" />}
        {discoverPending ? t('companyNew.scanning') : t('companyNew.scan')}
      </button>
    </div>

    {mutationError && <div className="alert-error text-sm">{t('companyNew.discoveryError', { message: mutationError })}</div>}

    {discoveryResult && (
      <section aria-label={t('companyNew.stackDetected')} className="overflow-hidden rounded-lg border border-surface-700 bg-surface-950/40">
        <div className="flex items-center gap-2 border-b border-surface-700 px-3 py-2">
          <CircleCheck size={15} aria-hidden="true" className="flex-shrink-0 text-emerald-300" />
          <span className="text-[13px] font-semibold text-gray-100">{t('companyNew.stackDetected')} — <span className="font-mono">{discoveryResult.name}</span></span>
        </div>
        <dl className="grid grid-cols-2 gap-px bg-surface-700 text-xs lg:grid-cols-4">
          {[
            { label: t('companyNew.primaryLanguage'), value: discoveryResult.language, icon: Code2 },
            { label: t('companyNew.frameworks'), value: discoveryResult.frameworks?.join(', ') || '—', icon: Puzzle },
            { label: t('companyNew.databases'), value: discoveryResult.databases?.join(', ') || '—', icon: Database },
            { label: t('companyNew.infrastructure'), value: discoveryResult.infra?.join(', ') || '—', icon: Server },
          ].map(({ label, value, icon: Icon }) => (
            <div key={label} className="bg-surface-900 px-3 py-2">
              <dt className="text-meta flex items-center gap-1"><Icon size={11} aria-hidden="true" />{label}</dt>
              <dd className="mt-0.5 break-words font-medium leading-snug text-gray-100" title={value}>{value}</dd>
            </div>
          ))}
        </dl>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-surface-700 px-3 py-2 text-xs">
          <span className="text-meta tabular-nums">{t('companyNew.filesScanned', { count: discoveryResult.file_count })}</span>
          <div className="flex flex-wrap items-center gap-1.5">
            {discoveryResult.team_source === 'ai' && (
              <StatusBadge icon={Sparkles} tone="brand" label={t('companyNew.aiSuggestion')} />
            )}
            {discoveryResult.team_source === 'rules' && (
              <StatusBadge icon={ListChecks} label={t('companyNew.rulesSuggestion')} />
            )}
            <StatusBadge icon={Gauge} tone={COMPLEXITY_TONE[discoveryResult.complexity] ?? 'neutral'}
              label={t('companyNew.complexity', { level: t(`companyNew.complexityLevels.${discoveryResult.complexity}`, { defaultValue: discoveryResult.complexity }) })} />
          </div>
        </div>
      </section>
    )}

    {!discoveryResult && (
      <EmptyState icon={FolderSearch} dashed title={t('companyNew.noProject')} description={t('companyNew.manualTeamHint')} />
    )}
  </div>
  )
}
