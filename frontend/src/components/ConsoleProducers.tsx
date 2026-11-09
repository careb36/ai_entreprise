import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Terminal } from 'lucide-react'
import { errorDetail } from '../services/api'
import { consoleProducersApi, type ConsoleProducers as Settings } from '../services/consoleProducers'

/**
 * Which terminal coding agent (Claude Code, Codex CLI, Gemini CLI, Cursor CLI)
 * produces a role's tasks instead of the model. The daemon runs it in the
 * task's worktree and the delivery is what it observed the CLI change, so it
 * needs the Git workflow. A Manager changes it; a Viewer reads it.
 */
export default function ConsoleProducers({ companyId, canManage }: { companyId: number; canManage: boolean }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const key = ['console-producers', companyId]
  const settings = useQuery({ queryKey: key, queryFn: () => consoleProducersApi.get(companyId) })
  const [draft, setDraft] = useState<Record<string, string> | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (settings.data) setDraft({ ...settings.data.producers })
  }, [settings.data])

  const save = useMutation({
    mutationFn: () => consoleProducersApi.save(companyId, Object.fromEntries(Object.entries(draft ?? {}).filter(([, v]) => v))),
    onSuccess: data => {
      queryClient.setQueryData(key, data)
      setSaved(true)
    },
  })

  if (settings.isLoading || !draft) {
    return settings.isError ? (
      <div role="alert" className="alert-error text-sm">{errorDetail(settings.error)}</div>
    ) : (
      <div role="status" className="flex items-center gap-2 text-sm text-gray-400">
        <Loader2 size={15} className="animate-spin" /> {t('common.loading')}
      </div>
    )
  }
  const data = settings.data as Settings
  const locked = !canManage || save.isPending || !data.git_workflow
  const cooling = data.clis.filter(cli => cli.cooldown_until)

  return (
    <section aria-labelledby="console-producers" className="card space-y-4">
      <div className="flex items-center gap-2">
        <Terminal size={18} className="text-brand-400" />
        <h2 id="console-producers" className="text-lg font-semibold">{t('consoleProducers.title')}</h2>
      </div>
      <p className="text-sm text-gray-400">{t('consoleProducers.subtitle')}</p>
      {!data.git_workflow && <p className="text-sm text-amber-300">{t('consoleProducers.needsGit')}</p>}
      {cooling.map(cli => (
        <p key={cli.id} className="text-xs text-amber-300">
          {t('consoleProducers.cooldown', { cli: cli.label, until: new Date(cli.cooldown_until as string).toLocaleString() })}
        </p>
      ))}
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-gray-400">
            <th className="py-1 pr-2 font-medium">{t('consoleProducers.role')}</th>
            <th className="py-1 font-medium">{t('consoleProducers.producer')}</th>
          </tr>
        </thead>
        <tbody>
          {data.roles.map(role => (
            <tr key={role} className="border-t border-surface-700">
              <td className="py-2 pr-2 font-medium">{role}</td>
              <td className="py-2">
                <label htmlFor={`console-producer-${role}`} className="sr-only">{role}</label>
                <select id={`console-producer-${role}`} className="input w-full" value={draft[role] ?? ''} disabled={locked}
                  onChange={e => { setSaved(false); setDraft(current => ({ ...current, [role]: e.target.value })) }}>
                  <option value="">{t('consoleProducers.model')}</option>
                  {data.clis.map(cli => <option key={cli.id} value={cli.id}>{`${cli.label} · ${cli.command}`}</option>)}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-gray-500">{t('consoleProducers.evidence')}</p>
      {canManage && (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className="btn-primary btn-sm" disabled={locked} onClick={() => save.mutate()}>
            {t('common.save')}
          </button>
          {saved && <span role="status" className="text-sm text-emerald-300">{t('consoleProducers.saved')}</span>}
          {save.isError && <span role="alert" className="text-sm text-red-300">{errorDetail(save.error)}</span>}
        </div>
      )}
    </section>
  )
}
