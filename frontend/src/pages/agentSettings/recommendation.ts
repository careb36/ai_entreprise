import type { ModelCatalogEntry } from '../../services/api'

export type Recommendation = {
  primary: ModelCatalogEntry | null
  alternatives: ModelCatalogEntry[]
  reasonKey: string
}

export const AUTO_RECOMMENDED_MODEL_IDS = new Set([
  'deepseek-coder',
  'deepseek-chat',
  'deepseek-reasoner',
  'gemini-3.8-flash',
  'gemini-3.1-pro-preview',
  'gemini-pro-latest',
  'gemini-flash-latest',
])

function normalizeRole(role: string): string {
  return role.trim().toLowerCase()
}

function pickModels(models: ModelCatalogEntry[], preferredIds: string[]): ModelCatalogEntry[] {
  const unique: ModelCatalogEntry[] = []
  const seen = new Set<string>()

  for (const id of preferredIds) {
    const match = models.find(model => model.id === id)
    if (match && !seen.has(match.id)) {
      seen.add(match.id)
      unique.push(match)
    }
  }

  return unique
}

/**
 * Returns ordered model IDs based on role + seniority.
 *
 * Seniority tiers:
 *   Staff / Senior  → strongest reasoning models first (higher cost, highest quality)
 *   Mid             → balanced models
 *   Junior          → fast/cheap models first (guided execution, cost-conscious)
 */
export function recommendModelForRole(
  models: ModelCatalogEntry[],
  role: string,
  seniority: string = 'Senior',
): Recommendation {
  const normalizedRole = normalizeRole(role)
  const level = seniority.trim().toLowerCase()
  const isJuniorMid = level === 'junior' || level === 'mid'
  const isStaffSenior = level === 'staff' || level === 'senior'

  const defaults = {
    ids: isJuniorMid
      ? ['gemini-3.8-flash', 'deepseek-chat']
      : ['gemini-3.8-flash', 'deepseek-chat'],
    reasonKey: 'generalist',
  }

  const plan = (() => {
    if (['backenddeveloper', 'backenddev', 'backend dev', 'backend', 'fullstackdev', 'fullstack dev', 'fullstack', 'dev', 'developer', 'mobiledev', 'mobile dev', 'mobile'].includes(normalizedRole)) {
      return {
        ids: isJuniorMid
          ? ['deepseek-coder', 'gemini-3.8-flash', 'deepseek-chat']
          : ['deepseek-reasoner', 'deepseek-coder', 'gemini-3.1-pro-preview'],
        reasonKey: 'dev',
      }
    }
    if (['frontenddeveloper', 'frontenddev', 'frontend dev', 'frontend'].includes(normalizedRole)) {
      return {
        ids: isJuniorMid
          ? ['deepseek-coder', 'gemini-3.8-flash', 'deepseek-chat']
          : ['gemini-3.1-pro-preview', 'deepseek-reasoner', 'deepseek-coder'],
        reasonKey: 'frontend',
      }
    }
    if (['dataengineer', 'data engineer', 'data', 'mlengineer', 'ml engineer', 'ml'].includes(normalizedRole)) {
      return {
        ids: isJuniorMid
          ? ['gemini-3.8-flash', 'deepseek-coder', 'deepseek-reasoner']
          : ['deepseek-reasoner', 'gemini-3.1-pro-preview', 'gemini-3.8-flash'],
        reasonKey: 'data',
      }
    }
    if (['designer', 'uxresearcher', 'ux researcher', 'ux'].includes(normalizedRole)) {
      return {
        ids: isJuniorMid
          ? ['gemini-3.8-flash', 'deepseek-chat', 'gemini-flash-latest']
          : ['gemini-3.1-pro-preview', 'gemini-3.8-flash', 'deepseek-reasoner'],
        reasonKey: 'designer',
      }
    }
    if (normalizedRole === 'qa') {
      return {
        ids: isJuniorMid
          ? ['deepseek-coder', 'gemini-3.8-flash', 'deepseek-chat']
          : ['deepseek-reasoner', 'gemini-3.1-pro-preview', 'deepseek-coder'],
        reasonKey: 'qa',
      }
    }
    if (normalizedRole === 'architect') {
      return {
        ids: isJuniorMid
          ? ['gemini-3.8-flash', 'deepseek-reasoner', 'gemini-3.1-pro-preview']
          : ['gemini-3.1-pro-preview', 'deepseek-reasoner', 'gemini-pro-latest'],
        reasonKey: 'architect',
      }
    }
    if (normalizedRole === 'po' || normalizedRole === 'product owner') {
      return {
        ids: isJuniorMid
          ? ['gemini-3.8-flash', 'deepseek-chat', 'gemini-3.1-pro-preview']
          : ['gemini-3.1-pro-preview', 'deepseek-reasoner', 'gemini-pro-latest'],
        reasonKey: 'po',
      }
    }
    if (normalizedRole === 'scrummaster' || normalizedRole === 'sm') {
      return {
        ids: isJuniorMid
          ? ['gemini-3.8-flash', 'deepseek-chat', 'deepseek-reasoner']
          : ['deepseek-reasoner', 'gemini-3.1-pro-preview', 'gemini-3.8-flash'],
        reasonKey: 'scrummaster',
      }
    }
    if (normalizedRole === 'devops') {
      return {
        ids: isJuniorMid
          ? ['gemini-3.8-flash', 'deepseek-coder', 'deepseek-reasoner']
          : ['deepseek-reasoner', 'gemini-3.1-pro-preview', 'gemini-3.8-flash'],
        reasonKey: 'devops',
      }
    }
    if (normalizedRole === 'security' || normalizedRole === 'secops') {
      return {
        ids: isJuniorMid
          ? ['gemini-3.8-flash', 'deepseek-reasoner', 'gemini-3.1-pro-preview']
          : ['deepseek-reasoner', 'gemini-3.1-pro-preview', 'gemini-3.8-flash'],
        reasonKey: 'security',
      }
    }
    if (['techwriter', 'tech writer', 'support'].includes(normalizedRole)) {
      return {
        ids: isStaffSenior
          ? ['gemini-3.8-flash', 'deepseek-chat', 'gemini-flash-latest']
          : ['gemini-3.8-flash', 'deepseek-chat', 'gemini-flash-latest'],
        reasonKey: 'support',
      }
    }
    return defaults
  })()

  const ordered = pickModels(models, plan.ids)
  return {
    primary: ordered[0] ?? null,
    alternatives: ordered.slice(1, 4),
    reasonKey: plan.reasonKey,
  }
}
