import { useTranslation } from 'react-i18next'
import type { DetectedStack } from '../../services/api'
import { FolderPicker } from '../../components/FolderPicker'
import { WorkspaceDaemonHelp } from '../../components/WorkspaceDaemonHelp'
import { DaemonStatusLine } from './DaemonStatusLine'
import { ProjectCard } from './ProjectCard'
import type { ProjectFolder } from './useProjectFolder'

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
      <h2 className="text-lg font-semibold text-white mb-1">{t('companyNew.scanTitle')}</h2>
      <p className="text-sm text-gray-400">
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
          className="btn btn-secondary flex-shrink-0">
          📂 {t('companyNew.browse')}
        </button>
      </div>
      {selectDirectory && (
        <button type="button" onClick={() => setPickerOpen(true)}
          className="text-xs text-brand-400 hover:text-brand-300 hover:underline">
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
        <span className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
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
        className="btn btn-primary flex-shrink-0 min-w-28">
        {discoverPending ? (
          <span className="flex items-center gap-2">
            <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
            {t('companyNew.scanning')}
          </span>
        ) : `🔍 ${t('companyNew.scan')}`}
      </button>
    </div>

    {mutationError && <div className="alert-error text-sm">{t('companyNew.discoveryError', { message: mutationError })}</div>}

    {discoveryResult && (
      <div className="rounded-xl border border-brand-700/40 bg-brand-950/20 overflow-hidden">
        <div className="px-4 py-3 border-b border-brand-700/20 flex items-center gap-2">
          <span className="text-green-400 text-lg">✓</span>
          <span className="text-sm font-semibold text-brand-200">{t('companyNew.stackDetected')} — <span className="font-mono">{discoveryResult.name}</span></span>
        </div>
        <div className="p-4 grid grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
          {[
            { label: t('companyNew.primaryLanguage'), value: discoveryResult.language, icon: '🔷' },
            { label: t('companyNew.frameworks'), value: discoveryResult.frameworks?.join(', ') || '—', icon: '🧩' },
            { label: t('companyNew.databases'), value: discoveryResult.databases?.join(', ') || '—', icon: '🗄️' },
            { label: t('companyNew.infrastructure'), value: discoveryResult.infra?.join(', ') || '—', icon: '⚙️' },
          ].map(({ label, value, icon }) => (
            <div key={label} className="bg-surface-800/70 rounded-lg p-3">
              <span className="text-base">{icon}</span>
              <p className="text-gray-500 mt-1">{label}</p>
              <p className="text-gray-200 font-medium mt-0.5 break-words leading-snug" title={value}>{value}</p>
            </div>
          ))}
        </div>
        <div className="px-4 py-2 border-t border-brand-700/20 flex items-center justify-between text-xs">
          <span className="text-gray-500">{t('companyNew.filesScanned', { count: discoveryResult.file_count })}</span>
          <div className="flex items-center gap-2">
            {discoveryResult.team_source === 'ai' && (
              <span className="badge bg-purple-900/40 text-purple-300">✨ {t('companyNew.aiSuggestion')}</span>
            )}
            {discoveryResult.team_source === 'rules' && (
              <span className="badge bg-surface-700 text-gray-400">{t('companyNew.rulesSuggestion')}</span>
            )}
            <span className={`badge ${discoveryResult.complexity === 'High' ? 'bg-red-900/40 text-red-300' : discoveryResult.complexity === 'Medium' ? 'bg-yellow-900/40 text-yellow-300' : 'bg-green-900/40 text-green-300'}`}>
              {t('companyNew.complexity', { level: t(`companyNew.complexityLevels.${discoveryResult.complexity}`, { defaultValue: discoveryResult.complexity }) })}
            </span>
          </div>
        </div>
      </div>
    )}

    {!discoveryResult && (
      <div className="rounded-xl border border-surface-700 bg-surface-800/30 p-5 text-center">
        <p className="text-3xl mb-2">🏗️</p>
        <p className="text-sm text-gray-400">{t('companyNew.noProject')}</p>
        <p className="text-xs text-gray-600 mt-1">{t('companyNew.manualTeamHint')}</p>
      </div>
    )}
  </div>
  )
}
