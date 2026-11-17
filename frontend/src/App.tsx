import { useEffect, useState } from 'react'
import { fetchHealth, type Health } from './api'
import AppShell from './app/AppShell'
import RunProvider from './app/RunProvider'
import { BrandMark } from './components/icons'

function App() {
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
          <h1>Stompbox Competitor Intelligence</h1>
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
