import { useEffect, useRef, useState } from 'react'
import type { Health } from '../api'
import DomainReview from '../pages/DomainReview'
import LiveRuns from '../pages/LiveRuns'
import Overview from '../pages/Overview'
import ProductIntelligence from '../pages/ProductIntelligence'
import { PAGE_TITLES, useRoute, type Page } from './router'
import { useRunContext } from './runContext'
import Sidebar from './Sidebar'
import TopBar from './TopBar'

export default function AppShell({ health }: { health: Health }) {
  const route = useRoute()
  const { runId } = useRunContext()
  const page: Page = route.page ?? (runId ? 'overview' : 'runs')
  const [navOpen, setNavOpen] = useState(false)
  const menuRef = useRef<HTMLButtonElement>(null)
  const mainRef = useRef<HTMLElement>(null)

  useEffect(() => {
    document.title = `${PAGE_TITLES[page]} · Stompbox Competitor Intelligence`
  }, [page])

  // Mobile drawer: Escape closes it and returns focus to the menu button.
  useEffect(() => {
    if (!navOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setNavOpen(false)
        menuRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navOpen])

  return (
    <div className="shell">
      <a
        className="skip-link"
        href="#main"
        onClick={(e) => {
          e.preventDefault() // keep the hash route; just move focus
          mainRef.current?.focus()
        }}
      >
        Skip to content
      </a>
      <Sidebar current={page} health={health} open={navOpen} onNavigate={() => setNavOpen(false)} />
      {navOpen && <div className="backdrop" aria-hidden="true" onClick={() => setNavOpen(false)} />}
      <div className="workspace">
        <TopBar menuRef={menuRef} page={page} navOpen={navOpen} onToggleNav={() => setNavOpen(!navOpen)} />
        <main id="main" ref={mainRef} tabIndex={-1} className="page">
          {page === 'overview' && <Overview />}
          {page === 'products' && <ProductIntelligence productId={route.param} />}
          {page === 'runs' && <LiveRuns />}
          {page === 'domains' && <DomainReview />}
        </main>
      </div>
    </div>
  )
}
