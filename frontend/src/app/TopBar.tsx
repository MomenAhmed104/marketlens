import type { Ref } from 'react'
import { MenuIcon } from '../components/icons'
import { CONNECTION_TEXT } from '../status'
import { PAGE_GROUPS, PAGE_TITLES, type Page } from './router'
import { useRunContext } from './runContext'
import RunSwitcher from './RunSwitcher'

interface Props {
  page: Page
  navOpen: boolean
  onToggleNav: () => void
  menuRef: Ref<HTMLButtonElement>
}

// The badge only appears when it adds information beyond the run's own status.
const SHOWN = new Set(['connecting', 'live', 'reconnecting', 'polling', 'expired', 'error'])

export default function TopBar({ page, navOpen, onToggleNav, menuRef }: Props) {
  const { view } = useRunContext()
  return (
    <header className="topbar">
      <button
        ref={menuRef}
        type="button"
        className="icon-btn menu-button"
        aria-label="Open navigation"
        aria-expanded={navOpen}
        aria-controls="sidebar"
        onClick={onToggleNav}
      >
        <MenuIcon />
      </button>
      <div className="page-heading">
        <p className="eyebrow">{PAGE_GROUPS[page]}</p>
        <h1 className="page-title">{PAGE_TITLES[page]}</h1>
      </div>
      <div className="topbar-run">
        <RunSwitcher />
        <span role="status" aria-live="polite" className="conn-slot">
          {SHOWN.has(view.connection) && (
            <span className={`conn conn-${view.connection}`}>
              <span className="conn-dot" aria-hidden="true" />
              {CONNECTION_TEXT[view.connection]}
            </span>
          )}
        </span>
      </div>
    </header>
  )
}
