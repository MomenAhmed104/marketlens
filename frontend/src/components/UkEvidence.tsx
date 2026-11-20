// UK relevance evidence (Option B), shared by Domain Review and Live Runs. The backend verifies
// LLM 1's quotes and decides sufficiency, contradiction and mixed location (rules.uk_review);
// these components only show that view. Verified evidence is kept apart from unverified claims,
// and nothing here approves a domain or grants benchmark permission.
import type { UkQuote, UkReview } from '../api'
import { TONE_ICON } from '../status'
import { UK_EVIDENCE_KIND_LABEL, ukEvidenceLine, ukEvidenceStatus } from '../ukRelevance'

/** The evidence status pill alone (compact contexts such as tables). */
export function UkStatusPill({ review: r }: { review: UkReview }) {
  const st = ukEvidenceStatus(r)
  return (
    <span className={`pill tone-${st.tone}`}>
      <span className="pill-icon" aria-hidden="true">{TONE_ICON[st.tone]}</span> {st.label}
    </span>
  )
}

/** UK relevance at a glance: what the verified evidence shows (never an approval) and one line. */
export function UkSummary({ review: r, compact = false }: { review: UkReview; compact?: boolean }) {
  return (
    <div className="uk-summary" aria-label="UK relevance">
      <UkStatusPill review={r} /> <span className="small">{ukEvidenceLine(r)}</span>
      {!compact && <p className="small muted uk-summary-text">{r.summary}</p>}
    </div>
  )
}

export function UkQuoteItem({ q, verified }: { q: UkQuote; verified: boolean }) {
  return (
    <li className="evidence-item">
      <span className="tag">{UK_EVIDENCE_KIND_LABEL[q.kind]}</span>{' '}
      {verified ? <span className="tag tag-ok">Verified on the page</span> : <span className="tag tag-warn">Not verified</span>}
      <blockquote>“{q.quote}”</blockquote>
      <div className="evidence-meta">
        <a href={q.url} target="_blank" rel="noreferrer noopener">{q.url}</a>
        {!verified && q.note && <> · {q.note}</>}
      </div>
    </li>
  )
}

/** UK relevance in full: verified evidence, what is missing, and LLM claims that did not hold up. */
export function UkDetails({ review: r, showSummary = false }: { review: UkReview; showSummary?: boolean }) {
  return (
    <div className="details uk-details">
      <h4>UK relevance</h4>
      {showSummary && <p className="small">{r.summary}</p>}
      <h5>Verified evidence</h5>
      {r.verified_evidence.length === 0 ? (
        <p className="small muted">No verified UK evidence.</p>
      ) : (
        <ul className="evidence" aria-label="Verified UK evidence">
          {r.verified_evidence.map((q) => <UkQuoteItem key={`${q.kind}|${q.url}|${q.quote}`} q={q} verified />)}
        </ul>
      )}
      {r.missing_evidence.length > 0 && (
        <>
          <h5>What would settle it</h5>
          <ul className="small" aria-label="Missing UK evidence">
            {r.missing_evidence.map((m) => <li key={m}>{m}</li>)}
          </ul>
        </>
      )}
      {r.unverified_claims.length > 0 && (
        <>
          <h5>Unverified claims (for context only, not facts)</h5>
          <ul className="evidence" aria-label="Unverified UK claims">
            {r.unverified_claims.map((q) => (
              <UkQuoteItem key={`${q.kind}|${q.url}|${q.quote}`} q={q} verified={false} />
            ))}
          </ul>
        </>
      )}
      {r.reasons.length > 0 && (
        <details className="small">
          <summary>How this was assessed</summary>
          <ul>{r.reasons.map((x) => <li key={x}>{x}</li>)}</ul>
        </details>
      )}
    </div>
  )
}
