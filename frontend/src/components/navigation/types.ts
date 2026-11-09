import type { ComponentType } from 'react'

export interface NavSubItem {
  id: string
  to: string
  labelKey: string
  defaultLabel: string
  description?: string
  icon?: ComponentType<{ size?: number; className?: string }>
  badgeCount?: number
}

export interface NavGroup {
  id: string
  section: string
  primaryTo: string
  labelKey: string
  defaultLabel: string
  icon: ComponentType<{ size?: number; className?: string }>
  badgeCount?: number
  items: NavSubItem[]
}

export interface NavigationProps {
  currentPath: string
  waitingReviewsCount?: number
  onNavigate?: () => void
}
