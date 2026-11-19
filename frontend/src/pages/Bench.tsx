import { useTranslation } from 'react-i18next'
import { FlaskConical, FolderX } from 'lucide-react'
import { BenchCardsTable, BenchSpecHistory } from '../components/bench/BenchHistory'
import { BenchLatestCard } from '../components/bench/BenchLatestCard'
import { HumanEvalPanel } from '../components/bench/HumanEvalPanel'
import { PromptSectionsTable } from '../components/bench/PromptSectionsTable'
import { EditFormatPanel } from '../components/bench/EditFormatPanel'
import { SweBenchPanel } from '../components/bench/SweBenchPanel'
import { EmptyState, LoadError, LoadingLine, PageHeader } from '../components/ui'
import { useBenchScorecards } from '../hooks/useBench'
import { errorDetail, type BenchValidCard } from '../services/api'

/**
 * The bench (issue #685, split from #316): the committed scorecards, graded by
 * tests a person wrote, newest first. The latest valid card in full, the
 * history of every card (an off-schema or unreadable one listed with why), each
 * specification across cards, and the prompt weight per section. Read-only;
 * a metric a card did not measure says so and is never shown as 0.
 */
export default function Bench() {
  const { t } = useTranslation()
  const query = useBenchScorecards()
  const cards = query.data?.cards ?? []
  const valid = cards.filter((c): c is BenchValidCard => c.status === 'valid')
  const latest = valid[0]

  return (
    <div className="page-container max-w-6xl space-y-4">
      <PageHeader title={t('bench.title')} subtitle={t('bench.subtitle')} />
      {query.isPending ? (
        <LoadingLine>{t('bench.loading')}</LoadingLine>
      ) : query.isError ? (
        <LoadError onRetry={() => query.refetch()}>{t('bench.loadError', { message: errorDetail(query.error) })}</LoadError>
      ) : !query.data.available ? (
        <EmptyState icon={FolderX} title={t('bench.unavailable')} description={t('bench.unavailableHint')} dashed />
      ) : cards.length === 0 ? (
        <EmptyState icon={FlaskConical} title={t('bench.empty')} dashed />
      ) : (
        <>
          {latest
            ? <BenchLatestCard card={latest} />
            : <EmptyState icon={FlaskConical} title={t('bench.noValidCard')} />}
          {latest?.prompt_sections && <PromptSectionsTable sections={latest.prompt_sections} />}
          {latest?.humaneval && <HumanEvalPanel data={latest.humaneval} />}
          {latest?.swebench && <SweBenchPanel data={latest.swebench} />}
          {latest?.edit_formats && <EditFormatPanel data={latest.edit_formats} />}
          <BenchCardsTable cards={cards} />
          {valid.length > 0 && <BenchSpecHistory cards={valid} />}
        </>
      )}
    </div>
  )
}
