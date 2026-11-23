// Search status of a discovered domain (Stage 8.1), shared by Domain Review and Live Runs.
// The backend decides; this only shows the decision and why. Benchmark permission is separate.
import type { ServesUkCustomers } from '../api'
import { explain } from '../plainLanguage'
import { TONE_ICON } from '../status'
import { SERVES_UK_LABEL, type SearchStatus } from '../searchEligibility'

export function SearchStatusPill({ status: st }: { status: SearchStatus }) {
  return (
    <span className={`pill tone-${st.tone}`}>
      <span className="pill-icon" aria-hidden="true">{TONE_ICON[st.tone]}</span> {st.label}
    </span>
  )
}

/** Searched or not, and why, in one line. */
export function SearchStatusSummary({ status: st, compact = false }: { status: SearchStatus; compact?: boolean }) {
  return (
    <div className="search-summary" aria-label="Search status">
      <SearchStatusPill status={st} />
      {!compact && <p className="small muted search-summary-text">{st.line}</p>}
    </div>
  )
}

/** The backend's full reasons and LLM 1's informational UK answer (never a gate). */
export function SearchStatusDetails(props: {
  status: SearchStatus
  servesUk?: ServesUkCustomers
  servesUkNote?: string | null
}) {
  const { status: st, servesUk, servesUkNote } = props
  return (
    <div className="details search-details">
      <h4>Why it is {st.kind === 'AUTOMATIC' || st.kind === 'MANUAL_APPROVAL' ? '' : 'not '}searched</h4>
      {st.reasons.length > 0 ? (
        <ul className="small" aria-label="Search eligibility reasons">
          {st.reasons.map((r) => <li key={r}>{explain(r)}</li>)}
        </ul>
      ) : (
        <p className="small">{st.line}</p>
      )}
      {servesUk && (
        <p className="small muted">
          {SERVES_UK_LABEL[servesUk]}
          {servesUkNote ? `: ${servesUkNote}` : ''}. For information only: it never decides whether the domain is searched.
        </p>
      )}
    </div>
  )
}
