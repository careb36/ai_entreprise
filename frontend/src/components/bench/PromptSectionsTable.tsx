import { useTranslation } from 'react-i18next'
import { Panel, TABLE, TD, TH, TR } from '../ui'
import { formatCount, formatShare } from '../../lib/benchFormat'
import type { BenchPromptSection } from '../../services/api'

/**
 * The card's prompt weight per section (schema v3): estimated tokens
 * (characters / 4, the backend's own measure, never the provider's count),
 * prompts that carried it, its share, and the prompts where the budget cut it.
 */
export function PromptSectionsTable({ sections }: { sections: BenchPromptSection[] }) {
  const { t, i18n } = useTranslation()
  const locale = i18n.language
  return (
    <Panel title={t('bench.sections.title')}>
      <p className="px-3 pt-2 text-xs text-gray-400">{t('bench.sections.note')}</p>
      <div className="overflow-x-auto">
        <table className={TABLE}>
          <caption className="sr-only">{t('bench.sections.caption')}</caption>
          <thead>
            <tr>
              <th scope="col" className={TH}>{t('bench.sections.section')}</th>
              <th scope="col" className={TH}>{t('bench.sections.tokens')}</th>
              <th scope="col" className={TH}>{t('bench.sections.share')}</th>
              <th scope="col" className={TH}>{t('bench.sections.prompts')}</th>
              <th scope="col" className={TH}>{t('bench.sections.cut')}</th>
            </tr>
          </thead>
          <tbody>
            {sections.map(row => (
              <tr key={row.section} className={TR}>
                <th scope="row" className={`${TD} font-medium`}><code>{row.section}</code></th>
                <td className={`${TD} tabular-nums`}>{formatCount(row.tokens, locale)}</td>
                <td className={`${TD} tabular-nums`}>{formatShare(row.share, locale)}</td>
                <td className={`${TD} tabular-nums`}>{formatCount(row.prompts, locale)}</td>
                <td className={`${TD} tabular-nums`}>{formatCount(row.cut, locale)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}
