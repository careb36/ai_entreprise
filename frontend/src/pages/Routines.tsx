import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Building2, CalendarClock, Plus } from 'lucide-react'
import { useAgents, useCurrentRole } from '../hooks/useQueries'
import { useRoutineActions, useRoutines } from '../hooks/useRoutines'
import { errorDetail } from '../services/api'
import { useAppStore } from '../store/useAppStore'
import { EmptyState, LoadError, LoadingRows, PageHeader, Panel } from '../components/ui'
import { emptyRoutine, RoutineForm } from './routines/RoutineForm'
import { RoutineRow, type RoutineHandlers } from './routines/RoutineRow'

// The role a new routine goes to when the company has hired none yet.
const FALLBACK_ROLE = 'Developer'

/**
 * Routines (`/company/routines`, issue #774): a task the company's agents run
 * on a schedule. Each run opens a small sprint that goes through the same run
 * window, budget, Verifier and QA as any other; while the previous run is
 * open, the next one is skipped. Every change is a revision a Manager can
 * restore. Any member reads them; a Manager changes them.
 */
export default function Routines() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const readOnly = useCurrentRole() === 'Viewer'
  const routines = useRoutines(companyId)
  const agents = useAgents(companyId)
  const actions = useRoutineActions(companyId)
  const [creating, setCreating] = useState(false)
  const roles = useMemo(
    () => [...new Set((agents.data?.agents ?? []).map(a => a.role).filter(Boolean))].sort(),
    [agents.data?.agents],
  )
  const failed = [actions.create, actions.update, actions.remove, actions.run, actions.restore].find(m => m.isError)
  const busy = [actions.create, actions.update, actions.remove, actions.run, actions.restore].some(m => m.isPending)

  const handlers: RoutineHandlers = {
    update: (routineId, data) => actions.update.mutate({ routineId, data }),
    remove: routineId => actions.remove.mutate(routineId),
    run: (routineId, values) => actions.run.mutateAsync({ routineId, values }),
    restore: (routineId, number) => actions.restore.mutate({ routineId, number }),
  }

  return (
    <div className="page-container max-w-5xl space-y-4">
      <PageHeader
        title={t('routines.title')}
        subtitle={t('routines.subtitle')}
        actions={!readOnly && companyId !== null && (
          <button type="button" className="btn-primary btn-sm" onClick={() => setCreating(c => !c)} aria-expanded={creating}>
            <Plus size={13} aria-hidden="true" /> {t('routines.new')}
          </button>
        )}
      />
      {companyId === null ? (
        <EmptyState icon={Building2} title={t('routines.noCompany')} dashed />
      ) : (
        <>
          {failed && <p role="alert" className="alert-error text-xs">{errorDetail(failed.error)}</p>}
          {creating && !readOnly && (
            <Panel title={t('routines.new')}>
              <RoutineForm
                initial={emptyRoutine(roles[0] ?? FALLBACK_ROLE)}
                roles={roles.length ? roles : [FALLBACK_ROLE]}
                saving={actions.create.isPending}
                submitLabel={t('routines.create')}
                onCancel={() => setCreating(false)}
                onSubmit={data => actions.create.mutate(data, { onSuccess: () => setCreating(false) })}
              />
            </Panel>
          )}
          {routines.isLoading ? (
            <LoadingRows />
          ) : routines.isError ? (
            <LoadError onRetry={() => routines.refetch()}>{t('routines.loadError')}</LoadError>
          ) : (routines.data?.routines ?? []).length === 0 ? (
            <EmptyState icon={CalendarClock} title={t('routines.empty')} dashed />
          ) : (
            <ul className="space-y-2">
              {(routines.data?.routines ?? []).map(routine => (
                <RoutineRow key={routine.id} routine={routine} roles={roles} readOnly={readOnly} busy={busy} handlers={handlers} />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
