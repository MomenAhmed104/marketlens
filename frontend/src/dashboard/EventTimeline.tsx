// Display-only view of the run's events, grouped by stage, newest first. Filtering
// never touches ingestion, de-duplication or recovery, which stay in runStream.ts.
import { useState } from 'react'
import type { CompetitorDiscoveredPayload, ExecutionEvent } from '../api'
import CompetitorClassPill from '../components/CompetitorClassPill'
import { fromBackendClass } from '../competitorClass'
import { plural, timeOnly } from '../format'
import { TONE_ICON } from '../status'
import { ukEventStatus } from '../ukRelevance'

type Group = 'lifecycle' | 'discovery' | 'search' | 'matching' | 'other'

const GROUP_LABELS: Record<Group, string> = {
  lifecycle: 'Run and stages',
  discovery: 'Discovery',
  search: 'Search and extraction',
  matching: 'Matching and prices',
  other: 'Other',
}

const GROUP_OF: Record<string, Group> = {
  run_started: 'lifecycle',
  run_completed: 'lifecycle',
  run_failed: 'lifecycle',
  run_cancelled: 'lifecycle',
  stage_started: 'lifecycle',
  stage_completed: 'lifecycle',
  product_loaded: 'lifecycle',
  product_completed: 'lifecycle',
  product_failed: 'lifecycle',
  competitor_discovered: 'discovery',
  domain_auto_approved: 'discovery',
  domain_approval_required: 'discovery',
  known_domains_loaded: 'discovery',
  search_started: 'search',
  page_retrieved: 'search',
  extraction_skipped: 'search',
  competitor_product_extracted: 'search',
  match_validated: 'matching',
  price_validated: 'matching',
}

const STAGE_TITLES: Record<string, string> = {
  validate: 'Validate',
  discovery: 'Discovery',
  review_queue: 'Review queue',
  targets: 'Search targets',
  product_search: 'Search and retrieval',
  extraction: 'Extraction',
  matching: 'Matching and prices',
}

const groupOf = (e: ExecutionEvent): Group => GROUP_OF[e.type] ?? 'other'
const LIMIT = 200

export default function EventTimeline({ events }: { events: ExecutionEvent[] }) {
  const [group, setGroup] = useState<Group | 'all'>('all')
  const [product, setProduct] = useState('')
  const [follow, setFollow] = useState(true)
  const [frozenAt, setFrozenAt] = useState(0) // highest seq shown while paused

  const products = [...new Set(events.map((e) => e.product_id).filter((p): p is string => !!p))]
  const shown = events
    .filter((e) => (follow || e.seq <= frozenAt) && (group === 'all' || groupOf(e) === group))
    .filter((e) => !product || e.product_id === product)
    .slice(-LIMIT)
    .reverse()
  const waiting = follow ? 0 : events.filter((e) => e.seq > frozenAt).length

  // Consecutive events of the same stage share one sticky header.
  const blocks: { key: string; title: string; items: ExecutionEvent[] }[] = []
  for (const e of shown) {
    const title = e.stage ? (STAGE_TITLES[e.stage] ?? e.stage) : 'Run'
    const last = blocks[blocks.length - 1]
    if (last && last.title === title) last.items.push(e)
    else blocks.push({ key: `${title}-${e.seq}`, title, items: [e] })
  }

  return (
    <div className="feed">
      <div className="feed-controls">
        <div className="chips" role="group" aria-label="Filter by event type">
          {(['all', 'lifecycle', 'discovery', 'search', 'matching'] as const).map((g) => (
            <button
              key={g}
              type="button"
              className="filter-chip"
              aria-pressed={group === g}
              onClick={() => setGroup(g)}
            >
              {g === 'all' ? 'All events' : GROUP_LABELS[g]}
            </button>
          ))}
        </div>
        <div className="feed-right">
          {products.length > 1 && (
            <select aria-label="Product" value={product} onChange={(e) => setProduct(e.target.value)}>
              <option value="">All products</option>
              {products.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          )}
          <label className="switch">
            <input
              type="checkbox"
              role="switch"
              checked={follow}
              onChange={(e) => {
                setFollow(e.target.checked)
                setFrozenAt(events.length ? events[events.length - 1].seq : 0)
              }}
            />
            <span className="switch-track" aria-hidden="true" />
            <span>Follow live</span>
          </label>
        </div>
      </div>
      <p className="hint-inline feed-count">
        {shown.length} of {plural(events.length, 'event')}
        {waiting > 0 && (
          <button type="button" className="new-pill" onClick={() => setFollow(true)}>
            {waiting} new · resume
          </button>
        )}
      </p>
      {shown.length === 0 ? (
        <p className="hint">No events{events.length ? ' match these filters' : ' yet'}.</p>
      ) : (
        <ol className="feed-list" aria-label="Run events, newest first">
          {blocks.map((b) => (
            <li key={b.key} className="feed-block">
              <h3 className="feed-stage">{b.title}</h3>
              <ol className="feed-items">
                {b.items.map((e) => (
                  <li key={e.seq} className={`feed-item fi-${groupOf(e)}`}>
                    <time dateTime={e.timestamp} className="fi-time">
                      {timeOnly(e.timestamp)}
                    </time>
                    <span className="fi-dot" aria-hidden="true" />
                    <div className="fi-body">
                      <span className="fi-type">{e.type.replace(/_/g, ' ')}</span>
                      {e.product_id && <span className="chip-sm">{e.product_id}</span>}
                      <p className="fi-message">{e.message}</p>
                      <DiscoveredClass event={e} />
                    </div>
                  </li>
                ))}
              </ol>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

/** A discovered domain's backend classification and UK evidence status, shown as soon as its event arrives. */
function DiscoveredClass({ event }: { event: ExecutionEvent }) {
  if (event.type !== 'competitor_discovered') return null
  const p = event.payload as Partial<CompetitorDiscoveredPayload>
  const cls = fromBackendClass(p.competitor_class)
  const uk = ukEventStatus(p) // older events carry no class or UK fields: show nothing for them
  if (!cls && !uk) return null
  return (
    <div className="fi-tags">
      {cls && <CompetitorClassPill cls={cls} />}
      {uk && (
        <span className="fi-uk" data-testid="uk-evidence">
          <span className={`pill tone-${uk.tone}`}>
            <span className="pill-icon" aria-hidden="true">{TONE_ICON[uk.tone]}</span> {uk.label}
          </span>{' '}
          <span className="small">{uk.line}</span>
        </span>
      )}
    </div>
  )
}
