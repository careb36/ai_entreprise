import React, { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Menu, ShieldCheck } from 'lucide-react'
import { SidebarNavigation } from './SidebarNavigation'
import { NAV_ITEMS, HICKS_LAW_NAV_LIMIT, filterNavByCapabilities } from './navConfig'

export interface GlobalNavProps {
  appName?: string
  logoUrl?: string
  waitingCount?: number
  activeTasksCount?: number
  capabilities?: { agent_graph?: boolean; memory?: boolean } | null
  children?: React.ReactNode
}

export const GlobalNav: React.FC<GlobalNavProps> = ({
  appName = 'La Fabrique',
  logoUrl = '/fabrique-icon.svg',
  waitingCount = 0,
  activeTasksCount = 0,
  capabilities = null,
  children,
}) => {
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()
  const navItems = filterNavByCapabilities(NAV_ITEMS, capabilities).slice(0, HICKS_LAW_NAV_LIMIT)

  return (
    <div className="flex min-h-screen flex-col bg-surface-950 text-gray-100">
      {/* Top Navbar Header */}
      <header className="sticky top-0 z-30 flex h-14 w-full items-center justify-between border-b border-surface-700 bg-surface-900 px-4 lg:px-6">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            className="rounded-lg p-2 text-gray-400 hover:bg-surface-800 hover:text-white lg:hidden"
            aria-label="Open mobile menu"
            data-testid="mobile-menu-toggle"
          >
            <Menu size={20} />
          </button>
          <Link to="/evidence" className="flex items-center gap-2.5 hover:opacity-90 transition-opacity">
            <img src={logoUrl} alt={appName} width={28} height={28} className="rounded-md" />
            <span className="font-bold text-white tracking-tight">{appName}</span>
          </Link>
        </div>

        {/* Quick Review / Governance Status Pill */}
        <div className="flex items-center gap-3">
          {waitingCount > 0 && (
            <Link
              to="/governance"
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/30 hover:bg-amber-500/30 transition-colors"
              aria-label={`${waitingCount} items waiting for review`}
            >
              <ShieldCheck size={14} />
              <span>{waitingCount} Review{waitingCount > 1 ? 's' : ''}</span>
            </Link>
          )}
        </div>
      </header>

      {/* Main Body with Hick's Law Sidebar and App Content */}
      <div className="flex flex-1 overflow-hidden">
        <SidebarNavigation
          isOpenMobile={mobileOpen}
          onCloseMobile={() => setMobileOpen(false)}
          badges={{
            waiting: waitingCount,
            activeTasks: activeTasksCount,
          }}
          capabilities={capabilities}
        />

        <main className="flex-1 overflow-y-auto p-4 lg:p-6">
          {children}
        </main>
      </div>
    </div>
  )
}
