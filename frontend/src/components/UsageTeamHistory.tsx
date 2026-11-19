import { useTranslation } from 'react-i18next'
import type { UsageTeamHistory as History } from '../services/api'

/** Which teams the report used (issue #702): the team each person was in at
 *  the time of the work, except for work from before the membership history
 *  started, which keeps today's team; the sentence names that period. */
export function UsageTeamHistory({ history }: { history?: History }) {
  const { t } = useTranslation()
  if (!history) return null
  const cover = history.current_teams_cover
  return (
    <p className="text-meta" data-testid="usage-team-history">
      {cover
        ? t('usage.teamHistory.partly', { since: cover.since, before: cover.before })
        : t('usage.teamHistory.atTheTime')}
    </p>
  )
}
