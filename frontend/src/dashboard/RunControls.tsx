import { useState, type FormEvent } from 'react'
import { MAX_PRODUCTS_PER_RUN, type SearchTarget } from '../api'
import { statusMeta } from '../status'

interface Props {
  selectedCount: number
  targets: SearchTarget[] | null
  active: boolean
  starting: boolean
  noProvider: boolean
  startError: string | null
  onStart: (opts: { run_discovery: boolean; domains: string[] | null }) => void
}

/** Sticky selection tray: run options and Start. Cancel lives in the run header. */
export default function RunControls(props: Props) {
  const { selectedCount, targets, active, starting, noProvider, startError } = props
  const [runDiscovery, setRunDiscovery] = useState(true)
  const [domains, setDomains] = useState<string[]>([])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    props.onStart({ run_discovery: runDiscovery, domains: domains.length ? domains : null })
  }
  const toggleDomain = (d: string) =>
    setDomains(domains.includes(d) ? domains.filter((x) => x !== d) : [...domains, d])

  const reason =
    selectedCount === 0
      ? 'Select at least one product.'
      : active
        ? 'A run is in progress. Wait for it to finish or cancel it.'
        : noProvider
          ? 'No search provider is configured on the backend.'
          : null

  return (
    <form className="tray" onSubmit={submit} aria-label="Start a run">
      <div className="tray-row">
        <p className="tray-count">
          <strong>
            {selectedCount} of {MAX_PRODUCTS_PER_RUN}
          </strong>{' '}
          selected
        </p>
        <label className="check">
          <input type="checkbox" checked={runDiscovery} onChange={(e) => setRunDiscovery(e.target.checked)} />
          <span>
            Discover new competitors
            <span className="hint">New domains go to review and are not searched until approved.</span>
          </span>
        </label>
        <div className="tray-actions">
          {reason && (
            <span id="start-reason" className="hint-inline">
              {reason}
            </span>
          )}
          <button
            type="submit"
            className="btn primary"
            disabled={reason !== null || starting}
            aria-describedby={reason ? 'start-reason' : undefined}
          >
            {starting ? 'Starting…' : `Start run (${selectedCount} product${selectedCount === 1 ? '' : 's'})`}
          </button>
        </div>
      </div>
      {targets && targets.length > 0 && (
        <details className="disclosure tray-domains">
          <summary>
            Search domains: {domains.length ? `${domains.length} chosen` : `all ${targets.length} eligible`}
          </summary>
          <fieldset className="domain-grid">
            <legend className="sr-only">Limit the search to these domains</legend>
            {targets.map((t) => (
              <label key={t.domain} className="check">
                <input type="checkbox" checked={domains.includes(t.domain)} onChange={() => toggleDomain(t.domain)} />
                <span>
                  {t.domain} <span className="hint-inline">{statusMeta(t.approval_status).label}</span>
                </span>
              </label>
            ))}
          </fieldset>
        </details>
      )}
      {startError && (
        <p role="alert" className="callout danger">
          Could not start the run: {startError}
        </p>
      )}
    </form>
  )
}
