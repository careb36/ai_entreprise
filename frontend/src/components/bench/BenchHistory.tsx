import { useTranslation } from 'react-i18next'
import { CheckCircle2, FileWarning, XCircle } from 'lucide-react'
import { CountPill, Panel, StatusBadge, TABLE, TD, TH, TR } from '../ui'
import { MeasureValue } from './MeasureValue'
import { SpecStatusBadge, useCriteriaFormat } from './BenchLatestCard'
import { formatCents, formatCount, formatStamp } from '../../lib/benchFormat'
import type { BenchCard, BenchSpec, BenchValidCard } from '../../services/api'

/** Whether the card matched the schema, as an icon and a word. */
export function CardStatusBadge({ status }: { status: BenchCard['status'] }) {
  const { t } = useTranslation()
  if (status === 'valid') return <StatusBadge icon={CheckCircle2} tone="success" label={t('bench.cardStatus.valid')} />
  if (status === 'invalid') return <StatusBadge icon={XCircle} tone="danger" label={t('bench.cardStatus.invalid')} />
  return <StatusBadge icon={FileWarning} tone="warning" label={t('bench.cardStatus.unreadable')} />
}

function useFolderLabel() {
  const { t } = useTranslation()
  return (folder: BenchCard['folder']) => t(`bench.folder.${folder || 'fake'}`)
}

/**
 * Every committed card, newest first: one row per card with its totals, and
 * the schema's problems for a card that is off it or could not be read (no
 * number is shown for those).
 */
export function BenchCardsTable({ cards }: { cards: BenchCard[] }) {
  const { t, i18n } = useTranslation()
  const criteria = useCriteriaFormat()
  const folderLabel = useFolderLabel()
  const count = (v: number) => formatCount(v, i18n.language)
  return (
    <Panel title={t('bench.history.title')}
      count={<CountPill count={cards.length} label={t('bench.history.countLabel', { count: cards.length })} />}>
      <div className="overflow-x-auto">
        <table className={TABLE}>
          <caption className="sr-only">{t('bench.history.caption')}</caption>
          <thead>
            <tr>
              <th scope="col" className={TH}>{t('bench.cols.stamp')}</th>
              <th scope="col" className={TH}>{t('bench.cols.source')}</th>
              <th scope="col" className={TH}>{t('bench.cols.card')}</th>
              <th scope="col" className={TH}>{t('bench.cols.criteria')}</th>
              <th scope="col" className={TH}>{t('bench.cols.tokens')}</th>
              <th scope="col" className={TH}>{t('bench.cols.specs')}</th>
            </tr>
          </thead>
          <tbody>
            {cards.map(card => (
              <tr key={`${card.folder}/${card.file}`} className={TR}>
                <th scope="row" className={`${TD} font-medium tabular-nums`}>
                  <span title={card.file}>{formatStamp(card.stamp, i18n.language)}</span>
                </th>
                <td className={TD}>{folderLabel(card.folder)}</td>
                <td className={TD}><CardStatusBadge status={card.status} /></td>
                {card.status === 'valid' ? (
                  <>
                    <td className={TD}><MeasureValue measure={card.totals.criteria} format={criteria} /></td>
                    <td className={TD}><MeasureValue measure={card.totals.tokens} format={count} /></td>
                    <td className={`${TD} tabular-nums`}>
                      {t('bench.history.specsCompleted', { completed: card.totals.specs_completed, total: card.totals.specs })}
                    </td>
                  </>
                ) : (
                  <td colSpan={3} className={TD}>
                    <ul className="list-disc space-y-0.5 pl-4 text-gray-300">
                      {card.problems.map(problem => <li key={problem}><code className="break-words">{problem}</code></li>)}
                    </ul>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

/**
 * Each specification across the valid cards, newest first: pass rate,
 * reported tokens and cost where the card measured them.
 */
export function BenchSpecHistory({ cards }: { cards: BenchValidCard[] }) {
  const { t, i18n } = useTranslation()
  const locale = i18n.language
  const criteria = useCriteriaFormat()
  const names = [...new Set(cards.flatMap(c => c.specs.map(s => s.name)))].sort()
  const rows = names.flatMap(name =>
    cards.flatMap(card => {
      const spec = card.specs.find(s => s.name === name)
      return spec ? [{ name, card, spec }] : []
    }))
  return (
    <Panel title={t('bench.specHistory.title')}>
      <div className="overflow-x-auto">
        <table className={TABLE}>
          <caption className="sr-only">{t('bench.specHistory.caption')}</caption>
          <thead>
            <tr>
              <th scope="col" className={TH}>{t('bench.cols.spec')}</th>
              <th scope="col" className={TH}>{t('bench.cols.stamp')}</th>
              <th scope="col" className={TH}>{t('bench.cols.status')}</th>
              <th scope="col" className={TH}>{t('bench.cols.criteria')}</th>
              <th scope="col" className={TH}>{t('bench.cols.tokens')}</th>
              <th scope="col" className={TH}>{t('bench.cols.cost')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ name, card, spec }: { name: string; card: BenchValidCard; spec: BenchSpec }) => (
              <tr key={`${name}/${card.folder}/${card.file}`} className={TR}>
                <th scope="row" className={`${TD} font-medium`}>{name}</th>
                <td className={`${TD} tabular-nums`}>{formatStamp(card.stamp, locale)}</td>
                <td className={TD}><SpecStatusBadge status={spec.status} /></td>
                <td className={TD}><MeasureValue measure={spec.criteria} format={criteria} /></td>
                <td className={TD}><MeasureValue measure={spec.tokens} format={v => formatCount(v, locale)} /></td>
                <td className={TD}><MeasureValue measure={spec.cost_cents} format={c => formatCents(c, locale)} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}
