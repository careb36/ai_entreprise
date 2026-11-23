import type { TFunction } from 'i18next'
import type { CompanyMembership } from '../services/api'

/**
 * The name to show for company *id*: the name the backend sends with the
 * person's membership, or "Company <id>" when it has none (an older backend,
 * or a company the person is not a member of).
 */
export function companyLabel(memberships: CompanyMembership[], id: number, t: TFunction): string {
  const name = memberships.find(m => m.company_id === id)?.company_name?.trim()
  return name || t('auth.companyPlaceholder', { id, defaultValue: 'Company {{id}}' })
}
