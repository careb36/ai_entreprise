import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ShieldAlert } from 'lucide-react'

/** What the backend observed of the run's company and its segregation-of-duties policy (`owner_action_sod.py`). */
export interface SodSituation {
  company_id: number | null
  new_company: boolean
  mode: string
  solo_exception: boolean
  solo_eligible: boolean | null
  solo_in_force: boolean
  approvals: string[]
  refused: string[]
}

export function sodOf(value: unknown): SodSituation | null {
  if (!value || typeof value !== 'object') return null
  const s = value as Partial<SodSituation>
  return Array.isArray(s.refused) && s.refused.length > 0 ? (s as SodSituation) : null
}

/** The link to the policy panel of *company* (the duties page, for that company). */
export function sodPolicyHref(company: number): string {
  return `/company/duties?company=${company}`
}

/**
 * Why segregation of duties refuses (or will refuse) the self-hosting run's
 * approvals, and what the Owner can do. It only explains and links: the
 * action never grants, changes or bypasses the policy (issue #701).
 */
export function SodNotice({ sod, stopped = false }: { sod: SodSituation; stopped?: boolean }) {
  const { t } = useTranslation()
  const mode = t(`duties.policy.modes.${sod.mode}`, { defaultValue: sod.mode })
  const actions = sod.refused.map(a => t(`ownerActions.sod.actions.${a}`, { defaultValue: a })).join(t('ownerActions.sod.and'))
  const company = sod.company_id

  return (
    <section role="status" className="space-y-1 rounded border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-200">
      <h3 className="flex items-center gap-1.5 font-semibold">
        <ShieldAlert size={12} aria-hidden="true" /> {t('ownerActions.sod.title')}
      </h3>
      {stopped && <p>{t('ownerActions.sod.stopped')}</p>}
      {company === null ? (
        <>
          <p>{t('ownerActions.sod.newCompany', { mode })}</p>
          <p>{t('ownerActions.sod.newCompanyNext')}</p>
        </>
      ) : (
        <>
          <p>{t('ownerActions.sod.blocked', { company, mode, actions })}</p>
          <p>{t(sod.solo_eligible ? 'ownerActions.sod.soloGrantable' : 'ownerActions.sod.soloNotEligible')}</p>
          <p>{t('ownerActions.sod.whatToDo', { company })}</p>
          {stopped && <p>{t('ownerActions.sod.rerun', { company })}</p>}
          <p>
            <Link to={sodPolicyHref(company)} className="font-semibold text-amber-100 underline">
              {t('ownerActions.sod.openPolicy', { company })}
            </Link>
          </p>
        </>
      )}
    </section>
  )
}
