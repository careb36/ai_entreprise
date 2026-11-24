/**
 * What the PO says is already done, on the plan preview (QuickLaunch).
 *
 * The PO checks the code with its read tools before it plans
 * (`services/planning/already_done.py`). What it says is done is its claim;
 * the files it read, with the sha256 the daemon reported, are what the
 * platform observed. The note shows both, labelled apart, and flags a path the
 * PO names but did not read in this planning turn.
 */
import { useTranslation } from 'react-i18next'
import { CheckCircle2 } from 'lucide-react'
import type { AlreadyDoneObserved, AlreadyDonePart } from '../services/api'

export function AlreadyDoneNote({ claim, observed }: {
  claim: AlreadyDonePart[] | null | undefined
  observed: AlreadyDoneObserved | null | undefined
}) {
  const { t } = useTranslation()
  if (!claim || claim.length === 0) return null
  const checked = new Map((observed?.claimed_paths ?? []).map(c => [c.path, c.read]))
  const files = observed?.files_read ?? []
  return (
    <section aria-label={t('alreadyDone.title')} className="rounded-lg border border-sky-600/40 bg-sky-950/20 p-2.5 text-[11px] text-sky-100 space-y-1.5">
      <p className="flex items-start gap-1.5 font-semibold">
        <CheckCircle2 size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
        {t('alreadyDone.title')}
      </p>
      <p className="text-sky-200/80">{t('alreadyDone.claimLabel')}</p>
      <ul className="list-disc space-y-1 pl-4">
        {claim.map((part, index) => (
          <li key={`${part.part}-${index}`} className="break-words">
            <span className="font-medium">{part.part}</span>
            {part.evidence && <span className="text-sky-200/80"> {t('alreadyDone.evidence', { evidence: part.evidence })}</span>}
            {part.paths.length > 0 && (
              <span className="mt-0.5 flex flex-wrap gap-1">
                {part.paths.map(path => (
                  <code key={path} className="rounded bg-surface-800 px-1 py-0.5 font-mono text-[10px] text-gray-300">
                    {path}
                    {!checked.get(path) && (
                      <span className="ml-1 rounded bg-amber-900/50 px-1 text-amber-300">{t('alreadyDone.notRead')}</span>
                    )}
                  </code>
                ))}
              </span>
            )}
          </li>
        ))}
      </ul>
      <p className="text-sky-200/80">{t('alreadyDone.observedLabel', { count: files.length })}</p>
      {files.length > 0 && (
        <ul className="space-y-0.5 pl-4">
          {files.map((file, index) => (
            <li key={`${file.path}-${index}`} className="break-all font-mono text-[10px] text-gray-300">
              {file.path} <span className="text-gray-500" title={file.sha256}>{t('alreadyDone.sha256', { sha256: file.sha256.slice(0, 12) })}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-sky-200/80">{t('alreadyDone.verify')}</p>
    </section>
  )
}
