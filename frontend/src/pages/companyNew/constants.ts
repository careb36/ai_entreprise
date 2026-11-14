import type { TFunction } from 'i18next'
import type { Permissions } from './types'

// Static display metadata keyed by tier, for a model not in the live catalogue (loading, legacy agents).
export const TIMEZONES = [
  'UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
  'America/Sao_Paulo', 'America/Argentina/Buenos_Aires', 'Europe/London', 'Europe/Paris',
  'Europe/Berlin', 'Asia/Tokyo', 'Asia/Singapore', 'Australia/Sydney',
]

export const approvalToggles = (t: TFunction) => [
  { key: 'require_approval_deploy', label: t('companyNew.approvalDeploy'), icon: '🚀' },
  { key: 'require_approval_db_write', label: t('companyNew.approvalDbWrite'), icon: '🗄️' },
]

export const resourceSummary = (t: TFunction) => [
  { label: t('companyNew.resources.git.short'), key: 'git_access', icon: '🔀' },
  { label: t('companyNew.resources.cloud.short'), key: 'cloud_access', icon: '☁️' },
  { label: t('companyNew.resources.db.short'), key: 'db_access', icon: '🗄️' },
  { label: t('companyNew.resources.ci.short'), key: 'ci_access', icon: '🔧' },
  { label: t('companyNew.resources.secrets.short'), key: 'secrets_access', icon: '🔑' },
]

export const DEFAULT_TIER = 'balanced'

export const TIER_META: Record<string, { color: string; icon: string; label: string }> = {
  strategic: { color: 'text-blue-300 bg-blue-900/30',   icon: '🔵', label: 'Strategic' },
  balanced:  { color: 'text-cyan-300 bg-cyan-900/30',   icon: '⚡', label: 'Balanced'  },
  code:      { color: 'text-green-300 bg-green-900/30', icon: '🟢', label: 'Code'      },
  fast:      { color: 'text-yellow-300 bg-yellow-900/30', icon: '🟡', label: 'Fast'    },
  preview:   { color: 'text-purple-300 bg-purple-900/30', icon: '🔮', label: 'Preview' },
}

// ── Constants ────────────────────────────────────────────────────────────────

export const ROLES = [
  { value: 'PO',       label: 'Product Owner',        icon: '🎯', desc: 'Defines features and priorities' },
  { value: 'SM',       label: 'Scrum Master',          icon: '🔄', desc: 'Facilitates agile delivery' },
  { value: 'Architect',label: 'Solution Architect',    icon: '🏗️', desc: 'Designs system structure' },
  { value: 'Dev',      label: 'Developer',             icon: '💻', desc: 'Writes and reviews code' },
  { value: 'BackendDeveloper',  label: 'Backend Developer',  icon: '🧩', desc: 'Server-side logic, APIs and services' },
  { value: 'FrontendDeveloper', label: 'Frontend Developer', icon: '🖥️', desc: 'User interfaces in the browser' },
  { value: 'Designer', label: 'UX/UI Designer',        icon: '🎨', desc: 'UX flows, UI specs and accessibility' },
  { value: 'TechWriter', label: 'Technical Writer',    icon: '📝', desc: 'Project and API documentation' },
  { value: 'QA',       label: 'QA Engineer',           icon: '🧪', desc: 'Tests and validates quality' },
  { value: 'DevOps',   label: 'DevOps Engineer',       icon: '🚀', desc: 'CI/CD and infrastructure' },
  { value: 'Security', label: 'Security Engineer',     icon: '🔐', desc: 'Vulnerability and compliance' },
  { value: 'Data',     label: 'Data Engineer',         icon: '📊', desc: 'Pipelines and analytics' },
]

export const SENIORITIES = [
  { value: 'Junior', label: 'Junior',  color: 'text-gray-400',   desc: '0–2 yrs' },
  { value: 'Mid',    label: 'Mid',     color: 'text-blue-400',   desc: '2–5 yrs' },
  { value: 'Senior', label: 'Senior',  color: 'text-brand-400',  desc: '5–10 yrs' },
  { value: 'Staff',  label: 'Staff',   color: 'text-purple-400', desc: '10+ yrs' },
]

export const AUTONOMY_LEVELS = [
  {
    value: 1,
    label: 'Level 1 — Copilot',
    icon: '🧑‍✈️',
    color: 'border-green-700/50 bg-green-950/20',
    badge: 'bg-green-900/40 text-green-300',
    desc: 'Every AI action requires explicit human approval. Safe for regulated environments.',
    features: ['Full human oversight', 'Manual approve/reject each task', 'Audit log for all actions'],
  },
  {
    value: 2,
    label: 'Level 2 — Exception Manager',
    icon: '⚡',
    color: 'border-brand-700/50 bg-brand-950/20',
    badge: 'bg-brand-900/40 text-brand-300',
    desc: 'AI runs routine tasks autonomously. Flags risky actions for human review.',
    features: ['Auto-approve low-risk tasks', 'Human review for deployments & DB writes', 'Configurable risk thresholds'],
  },
  {
    value: 3,
    label: 'Level 3 — Full Autonomous',
    icon: '🤖',
    color: 'border-red-700/40 bg-red-950/10',
    badge: 'bg-red-900/40 text-red-300',
    desc: 'AI operates without intervention. Recommended only after extensive testing.',
    features: ['Zero human intervention', 'Self-healing and auto-rollback', 'SLA-driven decisions'],
  },
]

export const PERM_LEVELS = {
  none:      { label: 'None',       color: 'text-gray-500' },
  read_only: { label: 'Read Only',  color: 'text-blue-400' },
  write:     { label: 'Write',      color: 'text-yellow-400' },
  admin:     { label: 'Admin',      color: 'text-red-400' },
}

export const DEFAULT_PERMISSIONS: Permissions = {
  git_access: 'read_only', cloud_access: 'none', db_access: 'read_only', ci_access: 'none', secrets_access: 'none',
  require_approval_deploy: true, require_approval_db_write: true,
}

export const STEPS = ['Project', 'Company', 'Autonomy', 'Permissions', 'Team', 'Review']

export const localizedRoles = (t: TFunction) => ROLES.map(role => ({
  ...role,
  label: t(`companyNew.roles.${role.value}.label`, { defaultValue: role.label }),
  desc: t(`companyNew.roles.${role.value}.description`, { defaultValue: role.desc }),
}))

export const localizedSeniorities = (t: TFunction) => SENIORITIES.map(seniority => ({
  ...seniority,
  label: t(`companyNew.seniorities.${seniority.value}.label`, { defaultValue: seniority.label }),
  desc: t(`companyNew.seniorities.${seniority.value}.experience`, { defaultValue: seniority.desc }),
}))

export const localizedAutonomy = (t: TFunction) => AUTONOMY_LEVELS.map(level => ({
  ...level,
  label: t(`companyNew.autonomy.${level.value}.label`, { defaultValue: level.label }),
  desc: t(`companyNew.autonomy.${level.value}.description`, { defaultValue: level.desc }),
  features: level.features.map((feature, index) =>
    t(`companyNew.autonomy.${level.value}.feature${index + 1}`, { defaultValue: feature })),
}))

export const localizedPermission = (t: TFunction, value: string) =>
  t(`companyNew.permissionLevels.${value}`, {
    defaultValue: PERM_LEVELS[value as keyof typeof PERM_LEVELS]?.label ?? value,
  })
