import { useState, useRef, useEffect, type FC, type KeyboardEvent } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronDown } from 'lucide-react'
import type { NavGroup } from './types'

interface NavDropdownProps {
  group: NavGroup
  isActive: boolean
  currentPath: string
  onNavigate?: () => void
}

export const NavDropdown: FC<NavDropdownProps> = ({
  group,
  isActive,
  currentPath,
  onNavigate,
}) => {
  const { t } = useTranslation()
  const [isOpen, setIsOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  const GroupIcon = group.icon
  const label = t(group.labelKey, { defaultValue: group.defaultLabel })

  // Close when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isOpen])

  // Keyboard navigation within the dropdown
  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      setIsOpen(false)
      triggerRef.current?.focus()
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!isOpen) {
        setIsOpen(true)
      } else {
        const items = menuRef.current?.querySelectorAll<HTMLAnchorElement>('[role="menuitem"]')
        if (items && items.length > 0) {
          const currentIndex = Array.from(items).indexOf(document.activeElement as HTMLAnchorElement)
          const nextIndex = (currentIndex + 1) % items.length
          items[nextIndex].focus()
        }
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (isOpen) {
        const items = menuRef.current?.querySelectorAll<HTMLAnchorElement>('[role="menuitem"]')
        if (items && items.length > 0) {
          const currentIndex = Array.from(items).indexOf(document.activeElement as HTMLAnchorElement)
          const nextIndex = (currentIndex - 1 + items.length) % items.length
          items[nextIndex].focus()
        }
      }
    }
  }

  return (
    <div
      ref={containerRef}
      className="relative inline-block text-left"
      onKeyDown={handleKeyDown}
      data-testid={`nav-group-${group.id}`}
    >
      <div className="flex items-center">
        {/* Primary Direct Link */}
        <Link
          to={group.primaryTo}
          onClick={onNavigate}
          aria-current={isActive ? 'page' : undefined}
          className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-l-lg transition-colors border-y border-l ${
            isActive
              ? 'text-white bg-brand-600/25 border-brand-500/40 shadow-sm'
              : 'text-gray-300 hover:text-white hover:bg-surface-800/80 border-transparent'
          }`}
        >
          <GroupIcon size={16} className={isActive ? 'text-brand-400' : 'text-gray-400'} />
          <span>{label}</span>

          {group.badgeCount !== undefined && group.badgeCount > 0 && (
            <span
              data-testid={`nav-badge-${group.id}`}
              className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-semibold bg-brand-500/30 text-brand-300 border border-brand-500/50"
            >
              <span aria-hidden="true">{group.badgeCount}</span>
              <span className="sr-only">
                {t('nav.badgeCount', { count: group.badgeCount, defaultValue: `${group.badgeCount} pending items` })}
              </span>
            </span>
          )}
        </Link>

        {/* Dropdown Toggle Caret */}
        <button
          ref={triggerRef}
          type="button"
          onClick={() => setIsOpen((prev) => !prev)}
          aria-expanded={isOpen}
          aria-haspopup="menu"
          aria-label={t('nav.toggleGroupMenu', { group: label, defaultValue: `Toggle ${label} menu` })}
          className={`px-1.5 py-2 text-sm rounded-r-lg border-y border-r transition-colors ${
            isActive
              ? 'text-brand-300 bg-brand-600/25 border-brand-500/40'
              : 'text-gray-400 hover:text-white hover:bg-surface-800/80 border-transparent'
          }`}
        >
          <ChevronDown
            size={14}
            className={`transition-transform duration-200 ${isOpen ? 'rotate-180 text-brand-400' : ''}`}
          />
        </button>
      </div>

      {/* Grouped Sub-Items Menu */}
      {isOpen && (
        <div
          ref={menuRef}
          role="menu"
          aria-label={label}
          data-testid={`dropdown-menu-${group.id}`}
          className="absolute left-0 top-full mt-1.5 w-64 rounded-xl border border-surface-700 bg-surface-900/95 backdrop-blur-md p-1.5 shadow-2xl z-50 animate-in fade-in slide-in-from-top-1 duration-150"
        >
          <div className="px-2.5 py-1.5 border-b border-surface-800 mb-1">
            <span className="text-[10px] font-bold uppercase tracking-wider text-gray-400">
              {label}
            </span>
          </div>

          <div className="space-y-0.5">
            {group.items.map((subItem) => {
              const SubIcon = subItem.icon
              const isSubActive = currentPath === subItem.to || currentPath.startsWith(`${subItem.to}/`)
              const subLabel = t(subItem.labelKey, { defaultValue: subItem.defaultLabel })

              return (
                <Link
                  key={subItem.id}
                  to={subItem.to}
                  role="menuitem"
                  onClick={() => {
                    setIsOpen(false)
                    onNavigate?.()
                  }}
                  className={`group flex items-start gap-2.5 px-2.5 py-2 rounded-lg text-xs transition-colors ${
                    isSubActive
                      ? 'bg-brand-600/20 text-white font-medium border border-brand-500/30'
                      : 'text-gray-300 hover:bg-surface-800 hover:text-white border border-transparent'
                  }`}
                >
                  {SubIcon && (
                    <SubIcon
                      size={15}
                      className={`mt-0.5 shrink-0 ${
                        isSubActive ? 'text-brand-400' : 'text-gray-400 group-hover:text-gray-200'
                      }`}
                    />
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between">
                      <span className="truncate">{subLabel}</span>
                      {subItem.badgeCount !== undefined && subItem.badgeCount > 0 && (
                        <span className="inline-flex items-center px-1.5 py-0.2 rounded-full text-[10px] font-semibold bg-brand-500/30 text-brand-300">
                          {subItem.badgeCount}
                        </span>
                      )}
                    </div>
                    {subItem.description && (
                      <p className="text-[11px] text-gray-500 truncate mt-0.5 group-hover:text-gray-400">
                        {subItem.description}
                      </p>
                    )}
                  </div>
                </Link>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
