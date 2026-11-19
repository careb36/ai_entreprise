import type { ModelCatalogEntry } from '../../services/api'

export type Recommendation = {
  primary: ModelCatalogEntry | null
  alternatives: ModelCatalogEntry[]
  reasonKey: string
  // False when the live catalogue does not list the recommended model; the
  // recommendation stands, and the panel says so instead of picking another
  // provider.
  primaryInCatalogue: boolean
}

// The default team's two models (backend services/models/model_factory.py):
// DeepSeek Pro where a mistake is expensive, DeepSeek Flash for the rest.
export const DEEPSEEK_PRO = 'deepseek-v4-pro'
export const DEEPSEEK_FLASH = 'deepseek-flash'
const PRO_ROLES = new Set(['architect', 'po', 'product owner', 'productowner', 'security', 'secops', 'reviewer'])

// Models a draft may hold because an earlier recommendation set them; a role
// or seniority change replaces them with the current recommendation. The
// retired DeepSeek ids and the Gemini ids earlier versions recommended stay
// here so those old automatic picks are replaced too.
export const AUTO_RECOMMENDED_MODEL_IDS = new Set([
  DEEPSEEK_PRO,
  DEEPSEEK_FLASH,
  'deepseek-coder',
  'deepseek-chat',
  'deepseek-reasoner',
  'gemini-3.8-flash',
  'gemini-3.1-pro-preview',
  'gemini-pro-latest',
  'gemini-flash-latest',
])

const REASON_KEYS: Array<[string[], string]> = [
  [['backenddeveloper', 'backenddev', 'backend dev', 'backend', 'fullstackdev', 'fullstack dev', 'fullstack', 'dev', 'developer', 'mobiledev', 'mobile dev', 'mobile'], 'dev'],
  [['frontenddeveloper', 'frontenddev', 'frontend dev', 'frontend'], 'frontend'],
  [['dataengineer', 'data engineer', 'data', 'mlengineer', 'ml engineer', 'ml'], 'data'],
  [['designer', 'uxresearcher', 'ux researcher', 'ux'], 'designer'],
  [['qa'], 'qa'],
  [['reviewer'], 'reviewer'],
  [['architect'], 'architect'],
  [['po', 'product owner', 'productowner'], 'po'],
  [['scrummaster', 'scrum master', 'sm'], 'scrummaster'],
  [['devops', 'sre'], 'devops'],
  [['security', 'secops'], 'security'],
  [['techwriter', 'tech writer', 'support'], 'support'],
]

function reasonKeyFor(role: string): string {
  for (const [roles, key] of REASON_KEYS) {
    if (roles.includes(role)) return key
  }
  return 'generalist'
}

function catalogueEntry(models: ModelCatalogEntry[], id: string): ModelCatalogEntry {
  return models.find(model => model.id === id)
    ?? { id, provider: 'deepseek', tier: id === DEEPSEEK_PRO ? 'strategic' : 'balanced', description: id }
}

/**
 * The recommended model for a role: the default team's, DeepSeek only.
 *
 * DeepSeek Pro for the Architect, PO, SecOps (Security) and Reviewer, DeepSeek
 * Flash for every other role, whatever the seniority; the single alternative
 * is the other DeepSeek model. When the live catalogue does not list the
 * recommended id it is still recommended (``primaryInCatalogue`` is false).
 * The seniority argument is kept for callers and no longer changes the pick.
 */
export function recommendModelForRole(
  models: ModelCatalogEntry[],
  role: string,
  _seniority: string = 'Senior',
): Recommendation {
  const normalizedRole = role.trim().toLowerCase()
  const lead = PRO_ROLES.has(normalizedRole) ? DEEPSEEK_PRO : DEEPSEEK_FLASH
  const other = lead === DEEPSEEK_PRO ? DEEPSEEK_FLASH : DEEPSEEK_PRO
  return {
    primary: catalogueEntry(models, lead),
    alternatives: [catalogueEntry(models, other)],
    reasonKey: reasonKeyFor(normalizedRole),
    primaryInCatalogue: models.some(model => model.id === lead),
  }
}
