import { useTranslation } from 'react-i18next'
import { LayoutDashboard, ShieldCheck, ClipboardCheck, Database, Code2 } from 'lucide-react'
import { useCapabilities } from '../../hooks/useQueries'

export type Section = 'evidence' | 'governance' | 'sprints' | 'generate' | 'memory' | 'company'

/**
 * The shell section a path belongs to. Exactly one element per route carries
 * `aria-current="page"`: the primary link of this section, or the Company menu
 * button for company-level pages.
 */
export function sectionForPath(pathname: string): Section | null {
  const path = pathname.toLowerCase()
  const under = (prefix: string) => path === prefix || path.startsWith(`${prefix}/`)
  if (under('/evidence') || under('/lineage')) return 'evidence'
  if (under('/governance')) return 'governance'
  if (under('/sprints') || under('/task')) return 'sprints'
  if (under('/generate')) return 'generate'
  if (under('/memory')) return 'memory'
  if (under('/agents') || under('/company') || under('/companies') || under('/support') || under('/bench')) return 'company'
  return null
}

/** The primary navigation links; Generate and Memory only when the backend offers them. */
export function useNavLinks() {
  const { t } = useTranslation()
  const { data, isPending, isError } = useCapabilities()
  return [
    { to: '/evidence', section: 'evidence' as Section, icon: ClipboardCheck, label: t('nav.evidence', { defaultValue: 'Evidence' }) },
    { to: '/governance', section: 'governance' as Section, icon: ShieldCheck, label: t('nav.review', { defaultValue: 'Review' }) },
    { to: '/sprints', section: 'sprints' as Section, icon: LayoutDashboard, label: t('nav.sprints', { defaultValue: 'Sprints' }) },
    ...(!isPending && !isError && data?.agent_graph === true ? [{ to: '/generate', section: 'generate' as Section, icon: Code2, label: t('nav.generate', { defaultValue: 'Generate' }) }] : []),
    ...(!isPending && !isError && data?.memory === true ? [{ to: '/memory', section: 'memory' as Section, icon: Database, label: t('nav.memory', { defaultValue: 'Memory' }) }] : []),
  ]
}
