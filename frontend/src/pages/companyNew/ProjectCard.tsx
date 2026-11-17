import { useTranslation } from 'react-i18next'
import { Folder } from 'lucide-react'
import type { WorkspaceInspection } from '../../services/api'

const PROVIDER_BADGES: Record<string, string> = {
  github: 'border-surface-600 text-gray-100',
  gitlab: 'border-orange-700/50 text-orange-200',
  bitbucket: 'border-blue-700/50 text-blue-200',
  other: 'border-surface-700 text-gray-200',
  none: 'border-amber-700/50 text-amber-200',
}

/** The folder the agents will work in, as the workspace daemon sees it. */
export function ProjectCard({ project }: { project: WorkspaceInspection }) {
  const { t } = useTranslation()
  if (!project.exists || !project.is_dir) {
    return (
      <div role="alert" className="alert-error text-sm">
        <span className="break-all">
          {t(project.exists ? 'projectFolder.notFolder' : 'projectFolder.notFound', {
            host: project.host.hostname,
            path: project.path,
          })}
        </span>
      </div>
    )
  }
  const git = project.git
  const provider = git?.is_repo ? (git.provider ?? 'other') : 'none'
  const remote = git?.is_repo ? (git.remote_url ?? git.web_url) : null
  return (
    <section aria-label={t('projectFolder.cardLabel')} data-testid="project-card"
      className="space-y-2 rounded-lg border border-surface-700 bg-surface-950/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Folder size={14} aria-hidden="true" className="flex-shrink-0 text-gray-400" />
        <span className="break-all text-[13px] font-semibold text-gray-100">{project.name}</span>
        <span className={`chip ${PROVIDER_BADGES[provider] ?? PROVIDER_BADGES.other}`} data-testid="project-provider">
          {t(`projectFolder.providers.${provider}`, { defaultValue: provider })}
        </span>
      </div>
      <p className="font-mono text-xs text-gray-400 break-all">{project.path}</p>
      {git?.is_repo ? (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-gray-400">{t('projectFolder.remote')}</dt>
          <dd className="min-w-0 break-all font-mono text-gray-200">
            {git.web_url ? (
              <a href={git.web_url} target="_blank" rel="noopener noreferrer" className="text-brand-300 hover:underline">
                {git.web_url}
              </a>
            ) : (remote ?? <span className="font-sans text-gray-400">{t('projectFolder.noRemote')}</span>)}
          </dd>
          <dt className="text-gray-400">{t('projectFolder.branch')}</dt>
          <dd className="min-w-0 break-all font-mono text-gray-200">
            {git.branch ?? <span className="font-sans text-gray-400">{t('projectFolder.noBranch')}</span>}
          </dd>
        </dl>
      ) : (
        <p className="text-xs text-gray-400">{t('projectFolder.notGitHint')}</p>
      )}
    </section>
  )
}
