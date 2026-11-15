import type { ModelCatalogEntry } from '../../services/api'
import type { Translate } from '../../i18n/translate'

export const COMMON_ROLES = [
  // Product & Design
  'PO', 'Designer', 'UXResearcher',
  // Engineering
  'Architect', 'Developer', 'BackendDeveloper', 'FrontendDeveloper', 'FullstackDev', 'MobileDev',
  'DataEngineer', 'MLEngineer',
  // Quality & Delivery
  'QA', 'ScrumMaster', 'DevOps', 'SecOps',
  // Support
  'TechWriter', 'Support',
]
export const SENIORITIES = ['Junior', 'Mid', 'Senior', 'Staff']
const TIER_META: Record<string, string> = {
  strategic: 'bg-blue-900/30 text-blue-300',
  balanced: 'bg-cyan-900/30 text-cyan-300',
  code: 'bg-green-900/30 text-green-300',
  fast: 'bg-yellow-900/30 text-yellow-300',
  preview: 'bg-purple-900/30 text-purple-300',
  reasoning: 'bg-fuchsia-900/30 text-fuchsia-300',
}

// Relative ordering used when grouping agents by role on screen.
const ROLE_DISPLAY_ORDER = [
  'PO',
  'Architect',
  'ScrumMaster',
  'Developer', 'BackendDeveloper', 'FrontendDeveloper', 'BackendDev', 'FrontendDev', 'FullstackDev', 'MobileDev',
  'DataEngineer', 'MLEngineer',
  'Designer', 'UXResearcher',
  'QA',
  'DevOps', 'SecOps', 'Security',
  'TechWriter', 'Support',
]

export function tierClass(tier?: string): string {
  return TIER_META[tier || 'balanced'] || 'bg-surface-700 text-gray-300'
}

export function groupedTiers(models: ModelCatalogEntry[]): string[] {
  return [...new Set(models.map(model => model.tier))]
}

export function formatRelativeTime(iso: string | null, t: Translate): string {
  if (!iso) return t('agentsConfig.never')
  const date = new Date(iso)
  const diffMs = Date.now() - date.getTime()
  if (Number.isNaN(diffMs)) return t('agentsConfig.never')
  if (diffMs < 60_000) return t('agentsConfig.timeJustNow')
  if (diffMs < 3_600_000) return t('agentsConfig.timeMinutesAgo', { n: Math.floor(diffMs / 60_000) })
  if (diffMs < 86_400_000) return t('agentsConfig.timeHoursAgo', { n: Math.floor(diffMs / 3_600_000) })
  if (diffMs < 30 * 86_400_000) return t('agentsConfig.timeDaysAgo', { n: Math.floor(diffMs / 86_400_000) })
  return date.toLocaleDateString()
}

export function formatCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return '—'
  if (usd === 0) return '$0'
  if (usd < 0.01) return '<$0.01'
  return `$${usd.toFixed(2)}`
}

// Stable role ordering: known roles in canonical order, then alphabetical for the rest.
export function sortRoles(roles: string[]): string[] {
  const ordered = ROLE_DISPLAY_ORDER.filter(r => roles.includes(r))
  const remaining = roles.filter(r => !ROLE_DISPLAY_ORDER.includes(r)).sort()
  return [...ordered, ...remaining]
}
