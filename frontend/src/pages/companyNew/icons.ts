// The wizard's icons, from the same set as the rest of the app (lucide), keyed
// by the values in ./constants: an autonomy level, a resource, an approval.
import {
  Bot, Cloud, Database, GitBranch, KeyRound, Rocket, UserCheck, Workflow, Zap, type LucideIcon,
} from 'lucide-react'

export const AUTONOMY_ICONS: Record<number, LucideIcon> = { 1: UserCheck, 2: Zap, 3: Bot }

export const RESOURCE_ICONS: Record<string, LucideIcon> = {
  git_access: GitBranch,
  cloud_access: Cloud,
  db_access: Database,
  ci_access: Workflow,
  secrets_access: KeyRound,
}

export const APPROVAL_ICONS: Record<string, LucideIcon> = {
  require_approval_deploy: Rocket,
  require_approval_db_write: Database,
}
