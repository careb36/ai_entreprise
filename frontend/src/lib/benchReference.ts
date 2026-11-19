/**
 * The bench's reference for a Sprints page tile (issue #686, split from #316):
 * the same number, read from the newest valid scorecard the bench committed
 * (`GET /v1/bench/scorecards`, newest first). It is what the bench's scorer
 * observed on the reference specifications, never a company's number.
 *
 * A metric the card did not measure has no reference (`null`), never a 0:
 * the pass rate when the grade did not run or had no criterion, the tokens
 * per task when no turn reported usage, and any per-task number when the
 * card ran no task. No valid card, no reference.
 */
import type { BenchCard, BenchValidCard } from '../services/api'

export type BenchMetric = 'passRate' | 'reworkPerTask' | 'blockedShare' | 'tokensPerTask'

export interface BenchReference {
  value: number
  /** The card's stamp (`20261114T201804Z`), for its date. */
  stamp: string
}

/** The newest valid card; the history is newest first. */
export function latestValidCard(cards: BenchCard[] | undefined): BenchValidCard | null {
  return cards?.find((card): card is BenchValidCard => card.status === 'valid') ?? null
}

function measuredValue(card: BenchValidCard, metric: BenchMetric): number | null {
  const { totals } = card
  if (metric === 'passRate') return totals.criteria.measured ? totals.criteria.value.rate : null
  // Every other number is per task: a card that ran no task measured none.
  if (totals.tasks <= 0) return null
  if (metric === 'reworkPerTask') return totals.rework_per_task
  if (metric === 'blockedShare') return totals.blocked_share
  return totals.tokens.measured ? totals.tokens.value / totals.tasks : null
}

export function benchReference(cards: BenchCard[] | undefined, metric: BenchMetric): BenchReference | null {
  const card = latestValidCard(cards)
  if (!card) return null
  const value = measuredValue(card, metric)
  return value === null || !Number.isFinite(value) ? null : { value, stamp: card.stamp }
}
