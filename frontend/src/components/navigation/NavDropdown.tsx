import React, { useRef, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { type NavChildItem } from './navConfig'

interface NavDropdownProps {
  isOpen: boolean
  onClose: () => void
  items: NavChildItem[]
  currentPath: string
  title: string
  anchorRef?: React.RefObject<HTMLElement>
}

export const NavDropdown: React.FC<NavDropdownProps> = ({
  isOpen,
  onClose,
  items,
  currentPath,
  title,
}) => {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!isOpen) return

    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        onClose()
      }
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [isOpen, onClose])

  if (!isOpen) return null

  return (
    <div
      ref={containerRef}
      role="menu"
      aria-label={`${title} sub-navigation`}
      className="absolute left-full top-0 ml-2 z-50 w-64 rounded-xl border border-surface-700 bg-surface-900/95 p-2 shadow-2xl backdrop-blur-md animate-in fade-in zoom-in-95 duration-150"
    >
      <div className="px-3 py-1.5 mb-1 border-b border-surface-800">
        <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">
          {title}
        </span>
      </div>
      <ul className="space-y-1">
        {items.map(item => {
          const isActive = currentPath === item.path
          return (
            <li key={item.id} role="none">
              <Link
                to={item.path}
                role="menuitem"
                onClick={onClose}
                aria-current={isActive ? 'page' : undefined}
                className={`group flex flex-col rounded-lg px-3 py-2 text-sm transition-colors ${
                  isActive
                    ? 'bg-brand-600/20 text-brand-300 font-medium'
                    : 'text-gray-300 hover:bg-surface-850 hover:text-white'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span>{item.label}</span>
                  {item.badge !== undefined && (
                    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-brand-500/20 text-brand-300">
                      {item.badge}
                    </span>
                  )}
                </div>
                {item.description && (
                  <span className="text-[11px] text-gray-400 group-hover:text-gray-300 line-clamp-1 mt-0.5">
                    {item.description}
                  </span>
                )}
              </Link>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
