import { useEffect, useState } from 'react'
import { fetchHealth, type Health } from './api'
import Review from './Review'

// Backend status plus the Phase 4 competitor review section.
// The catalogue/run dashboard is built in Phase 8.
function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchHealth().then(setHealth, (e: Error) => setError(e.message))
  }, [])

  return (
    <main>
      <h1>Stompbox Competitor Intelligence</h1>
      {error && <p role="alert">Backend unavailable: {error}</p>}
      {!error && !health && <p>Connecting to backend…</p>}
      {health && (
        <p>
          Backend {health.status}: {health.products} catalogue products,{' '}
          {health.competitors} configured competitors.
        </p>
      )}
      {health && <Review />}
    </main>
  )
}

export default App
