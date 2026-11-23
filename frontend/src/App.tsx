import { lazy, Suspense, useEffect, useState, useSyncExternalStore } from 'react'
import { fetchHealth, type Health } from './api'
import AppShell from './app/AppShell'
import RunProvider from './app/RunProvider'
import { isLandingRoute } from './app/router'
import { BrandMark } from './components/icons'

// The landing page is its own chunk (with its own isolated CSS), so the dashboard bundle is unchanged.
const Landing = lazy(() => import('./landing/Landing'))

const subscribe = (cb: () => void) => {
  window.addEventListener('hashchange', cb)
  window.addEventListener('popstate', cb)
  return () => {
    window.removeEventListener('hashchange', cb)
    window.removeEventListener('popstate', cb)
  }
}
const locationKey = () => `${window.location.search}${window.location.hash}`

/** Landing page at / (and #/home); the dashboard on its own hash routes, unchanged. */
function App() {
  useSyncExternalStore(subscribe, locationKey)
  if (isLandingRoute(window.location.hash, window.location.search)) {
    return (
      <Suspense fallback={<div className="lp-loading" aria-busy="true" />}>
        <Landing />
      </Suspense>
    )
  }
  return <Dashboard />
}

/** The existing dashboard: needs the backend; the landing page does not. */
function Dashboard() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchHealth().then(setHealth, (e: Error) => setError(e.message))
  }, [])

  if (error || !health) {
    return (
      <main className="boot">
        <div className="boot-card">
          <BrandMark />
          <h1>MarketLens</h1>
          {error ? (
            <p role="alert" className="callout danger">
              Backend unavailable: {error}
            </p>
          ) : (
            <p className="hint">Connecting to backend…</p>
          )}
        </div>
      </main>
    )
  }

  return (
    <RunProvider>
      <AppShell health={health} />
    </RunProvider>
  )
}

export default App
