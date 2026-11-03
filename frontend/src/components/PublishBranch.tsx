/**
 * Publishing a run's task branch the way a pull request starts: the
 * workspace daemon pushes it to the repository's origin with the person's
 * own Git credentials and opens a draft pull request (`gh`) or merge request
 * (`glab`), or gives the page where one is opened. The description carries
 * the run's evidence. Everyone sees where it was published; Managers publish,
 * and bring back the comments people left on the request there.
 */
import { useTranslation } from 'react-i18next'
import { ExternalLink, GitPullRequest, Loader2, Download } from 'lucide-react'
import { errorDetail } from '../services/api'
import { type RunPublication, usePublishRun, useReviewComments, useRunPublications, useSyncComments } from '../services/runReviews'
import { CommentThread } from './LineComments'

function Publication({ publication, companyId, runId, canPublish }: {
  publication: RunPublication; companyId: number; runId: number; canPublish: boolean
}) {
  const { t } = useTranslation()
  const sync = useSyncComments(companyId, runId)
  // Only a request the platform's CLI opened has comments to read.
  const readable = publication.how !== 'compare' && publication.url !== null
  return (
    <li className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
      <span className="font-mono">
        {t('review.publish.pushed', {
          name: publication.published_by,
          commit: publication.commit.slice(0, 7),
          branch: publication.branch,
          base: publication.base,
        })}
      </span>
      {publication.url ? (
        <a href={publication.url} target="_blank" rel="noopener noreferrer"
          className="flex items-center gap-1 text-brand-300 hover:underline">
          {t(`review.publish.how.${publication.how}`)}
          <ExternalLink size={12} aria-hidden="true" />
        </a>
      ) : (
        <span>{t('review.publish.noUrl')}</span>
      )}
      {publication.url && publication.updated && <span>{t('review.publish.updated')}</span>}
      {canPublish && readable && (
        <button type="button" onClick={() => sync.mutate(publication.id)} disabled={sync.isPending}
          className="btn btn-ghost btn-sm flex items-center gap-1">
          {sync.isPending ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} aria-hidden="true" />}
          {t('review.publish.sync')}
        </button>
      )}
      {sync.isSuccess && (
        <span role="status">{t('review.publish.synced', { added: sync.data.added, updated: sync.data.updated })}</span>
      )}
      {sync.isSuccess && sync.data.truncated && (
        <span role="note" className="text-amber-300">{t('review.publish.truncated')}</span>
      )}
      {sync.isError && <span role="alert" className="text-red-300">{errorDetail(sync.error)}</span>}
    </li>
  )
}

export function PublishBranch({ companyId, runId, branch, canPublish, shownPaths = [] }: {
  companyId: number; runId: number; branch: string; canPublish: boolean
  /** The files Files changed shows: a comment on one of their lines appears there instead. */
  shownPaths?: string[]
}) {
  const { t } = useTranslation()
  const publications = useRunPublications(companyId, runId).data?.publications ?? []
  const comments = useReviewComments(companyId, runId).data?.comments ?? []
  const shown = new Set(shownPaths)
  const elsewhere = comments.filter(c => c.source && (c.path === null || c.line === null || !shown.has(c.path)))
  const publish = usePublishRun(companyId, runId)
  if (!canPublish && publications.length === 0) return null
  return (
    <section aria-label={t('review.publish.title')} className="card space-y-2 p-3">
      {publications.length > 0 && (
        <ul className="space-y-1">
          {publications.map(publication => (
            <Publication key={publication.id} publication={publication} companyId={companyId} runId={runId} canPublish={canPublish} />
          ))}
        </ul>
      )}
      {elsewhere.length > 0 && (
        <div className="space-y-2 border-t border-surface-700 pt-2">
          <p className="text-xs text-gray-400">{t('review.publish.fromPlatform')}</p>
          <CommentThread comments={elsewhere} />
        </div>
      )}
      {canPublish && (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => publish.mutate()} disabled={publish.isPending}
            className="btn btn-primary btn-sm flex items-center gap-1.5">
            {publish.isPending ? <Loader2 size={13} className="animate-spin" /> : <GitPullRequest size={13} aria-hidden="true" />}
            {publications.length > 0 ? t('review.publish.again') : t('review.publish.button')}
          </button>
          <p className="min-w-0 flex-1 text-xs text-gray-500">{t('review.publish.hint', { branch })}</p>
        </div>
      )}
      {publish.isError && <p role="alert" className="text-sm text-red-300">{errorDetail(publish.error)}</p>}
    </section>
  )
}
