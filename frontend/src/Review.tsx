// Phase 4: competitor domain review. Discovered domains are reviewed here;
// decisions persist on the backend. Not the Phase 8 run dashboard.
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  approveCandidate,
  BENCHMARK_ELIGIBLE_TYPES,
  COMPANY_TYPES,
  fetchCandidate,
  fetchCandidates,
  rejectCandidate,
  runDiscovery,
  updateDecision,
  type CandidateDetail,
  type CandidateSummary,
  type CompanyType,
  type DiscoveryRunSummary,
  type DomainDecision,
  type ReviewDecision,
  type ReviewStatus,
} from './api'

type Filter = ReviewStatus | 'ALL'
const FILTERS: Filter[] = ['PENDING', 'APPROVED', 'REJECTED', 'ALL']
const UNKNOWN = '__unknown__'
const REVIEWER_KEY = 'stompbox.reviewer'

const typeLabel = (t: CompanyType | null) => (t ? t.replace('_', ' ') : 'Unknown type')
const pct = (v: number | null) => (v === null ? 'not assessed' : v.toFixed(2))
const when = (iso: string) => new Date(iso).toLocaleString()

export default function Review() {
  const [candidates, setCandidates] = useState<CandidateSummary[] | null>(null)
  const [filter, setFilter] = useState<Filter>('PENDING')
  const [error, setError] = useState<string | null>(null)
  const [reviewer, setReviewer] = useState(() => localStorage.getItem(REVIEWER_KEY) ?? '')

  const reload = useCallback(() => {
    fetchCandidates().then(
      (c) => {
        setCandidates(c)
        setError(null)
      },
      (e: Error) => setError(e.message),
    )
  }, [])

  useEffect(reload, [reload])
  useEffect(() => localStorage.setItem(REVIEWER_KEY, reviewer), [reviewer])

  const counts = (f: Filter) =>
    candidates?.filter((c) => f === 'ALL' || c.review_status === f).length ?? 0
  const shown = candidates?.filter((c) => filter === 'ALL' || c.review_status === filter) ?? []

  return (
    <section aria-labelledby="review-heading">
      <h2 id="review-heading">Competitor domain review</h2>
      <p className="muted">
        Discovered companies need a reviewer decision before they can be used in targeted
        product searches. The 13 configured competitors are not listed here and stay unchanged.
      </p>

      <DiscoveryRunner onDone={reload} />

      <label className="field inline">
        Reviewer name{' '}
        <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="Your name" />
      </label>

      <div className="tabs" role="tablist" aria-label="Filter by review status">
        {FILTERS.map((f) => (
          <button
            key={f}
            role="tab"
            aria-selected={filter === f}
            className={filter === f ? 'tab active' : 'tab'}
            onClick={() => setFilter(f)}
          >
            {f === 'ALL' ? 'All' : f.charAt(0) + f.slice(1).toLowerCase()} ({counts(f)})
          </button>
        ))}
      </div>

      {error && <p role="alert" className="error">{error}</p>}
      {!candidates && !error && <p>Loading review queue…</p>}
      {candidates && shown.length === 0 && (
        <p className="muted">
          {candidates.length === 0
            ? 'No discovered companies yet. Run discovery for one or more products above.'
            : 'Nothing in this list.'}
        </p>
      )}
      <ul className="cards">
        {shown.map((c) => (
          <CandidateCard key={c.domain} candidate={c} reviewer={reviewer} onChanged={reload} />
        ))}
      </ul>
    </section>
  )
}

function DiscoveryRunner({ onDone }: { onDone: () => void }) {
  const [ids, setIds] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<DiscoveryRunSummary | null>(null)
  const [error, setError] = useState<string | null>(null)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const productIds = ids.split(/[\s,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean)
    if (productIds.length === 0) return
    setBusy(true)
    setError(null)
    runDiscovery(productIds)
      .then((r) => {
        setResult(r)
        onDone()
      }, (e: Error) => setError(e.message))
      .finally(() => setBusy(false))
  }

  return (
    <form className="panel" onSubmit={submit}>
      <label className="field inline">
        Product IDs{' '}
        <input value={ids} onChange={(e) => setIds(e.target.value)} placeholder="SB-001, SB-044" />
      </label>{' '}
      <button type="submit" disabled={busy}>
        {busy ? 'Running discovery…' : 'Run discovery'}
      </button>
      {error && <p role="alert" className="error">{error}</p>}
      {result && (
        <div className="muted small">
          <p>
            Run {result.run_id}: {result.candidates.length} candidate(s), mode{' '}
            <strong>{result.mode}</strong>
            {result.llm_disabled_reason && ` (${result.llm_disabled_reason})`}.
          </p>
          {result.warnings.length > 0 && (
            <ul>
              {result.warnings.map((w) => <li key={w}>{w}</li>)}
            </ul>
          )}
        </div>
      )}
    </form>
  )
}

function StatusBadge({ status }: { status: ReviewStatus }) {
  return <span className={`badge status-${status.toLowerCase()}`}>{status}</span>
}

function CandidateCard(props: { candidate: CandidateSummary; reviewer: string; onChanged: () => void }) {
  const { candidate: c, reviewer, onChanged } = props
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<CandidateDetail | null>(null)
  const [mode, setMode] = useState<'approve' | 'reject' | 'update' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const d = c.decision

  useEffect(() => {
    if (!open) return
    fetchCandidate(c.domain).then(setDetail, (e: Error) => setError(e.message))
  }, [open, c.domain, c.decision?.version])

  const name = d?.company_name ?? (c.name_verified ? c.proposed_company_name : null)
  const shownType = d ? d.company_type : c.proposed_company_type

  return (
    <li className="card">
      <div className="card-head">
        <div>
          <strong>{name ?? 'Company name unknown'}</strong>{' '}
          <a href={c.homepage_url} target="_blank" rel="noreferrer noopener">{c.domain}</a>
        </div>
        <StatusBadge status={c.review_status} />
      </div>

      <div className="badges">
        <span className={shownType ? 'badge' : 'badge warn'}>
          {typeLabel(shownType)}
          {!d && shownType && ' (LLM proposal)'}
        </span>
        {!c.llm_classified && (
          <span className="badge warn" title="Discovered without LLM classification; nothing was inferred.">
            Unclassified · discovered in no-LLM mode
          </span>
        )}
        {c.known_marketplace && <span className="badge warn">Known marketplace</span>}
        <span className={c.search_eligible ? 'badge ok' : 'badge'}>
          {c.search_eligible ? 'Used in targeted searches' : 'Not searched'}
        </span>
        <span className={c.benchmark_eligible ? 'badge ok' : 'badge'}>
          {c.benchmark_eligible ? 'Price benchmark' : 'Not a price benchmark'}
        </span>
      </div>

      <dl className="facts">
        <dt>Confidence</dt><dd>{pct(c.confidence)}</dd>
        <dt>Relevance</dt><dd>{pct(c.relevance)}</dd>
        <dt>UK signals</dt><dd>{c.uk_signals.join(', ') || 'none found'}</dd>
        <dt>Products found</dt><dd>{c.products_matched.join(', ') || 'none'}</dd>
        <dt>Evidence</dt><dd>{c.evidence_count} item(s), seen in {c.times_seen} run(s)</dd>
        <dt>Automatic result</dt><dd>{c.automatic_status}</dd>
      </dl>

      {d && (
        <p className="small">
          {d.decision} by <strong>{d.reviewer}</strong> on {when(d.decided_at)}
          {d.version > 1 && ` (version ${d.version})`}
          {d.notes && <> · “{d.notes}”</>}
        </p>
      )}

      <div className="actions">
        {c.review_status === 'PENDING' ? (
          <>
            <button onClick={() => setMode(mode === 'approve' ? null : 'approve')}>Approve…</button>
            <button onClick={() => setMode(mode === 'reject' ? null : 'reject')}>Reject…</button>
          </>
        ) : (
          <button onClick={() => setMode(mode === 'update' ? null : 'update')}>Change decision…</button>
        )}
        <button className="link" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? 'Hide evidence and history' : 'Show evidence and history'}
        </button>
      </div>

      {mode && (
        <DecisionForm
          mode={mode}
          candidate={c}
          reviewer={reviewer}
          onCancel={() => setMode(null)}
          onSaved={() => {
            setMode(null)
            onChanged()
          }}
        />
      )}
      {error && <p role="alert" className="error">{error}</p>}
      {open && detail && <Details detail={detail} />}
      {open && !detail && !error && <p className="muted">Loading evidence…</p>}
    </li>
  )
}

function DecisionForm(props: {
  mode: 'approve' | 'reject' | 'update'
  candidate: CandidateSummary
  reviewer: string
  onCancel: () => void
  onSaved: (d: DomainDecision) => void
}) {
  const { mode, candidate: c, reviewer, onCancel, onSaved } = props
  const d = c.decision
  const [decision, setDecision] = useState<ReviewDecision>(
    mode === 'reject' ? 'REJECTED' : mode === 'approve' ? 'APPROVED' : (d?.decision ?? 'APPROVED'),
  )
  // '' = not chosen yet: the reviewer must pick a type, or explicitly pick "unknown".
  const [type, setType] = useState<string>(d ? (d.company_type ?? UNKNOWN) : '')
  const [name, setName] = useState(d?.company_name ?? (c.name_verified ? (c.proposed_company_name ?? '') : ''))
  const [benchmark, setBenchmark] = useState(d?.benchmark_eligible ?? false)
  const [notes, setNotes] = useState(d?.notes ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const companyType = type === UNKNOWN || type === '' ? null : (type as CompanyType)
  const approving = decision === 'APPROVED'
  const canBenchmark = approving && companyType !== null && BENCHMARK_ELIGIBLE_TYPES.includes(companyType)
  const ready = reviewer.trim() !== '' && (!approving || type !== '')

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const input = {
      reviewer: reviewer.trim(),
      company_type: companyType,
      company_name: name.trim() || null,
      benchmark_eligible: canBenchmark && benchmark,
      notes: notes.trim() || null,
    }
    setBusy(true)
    setError(null)
    const call =
      mode === 'approve'
        ? approveCandidate(c.domain, input)
        : mode === 'reject'
          ? rejectCandidate(c.domain, {
              reviewer: input.reviewer,
              company_type: input.company_type,
              company_name: input.company_name,
              notes: input.notes,
            })
          : updateDecision(c.domain, { ...input, decision })
    call.then(onSaved, (e: Error) => setError(e.message)).finally(() => setBusy(false))
  }

  return (
    <form className="panel decision" onSubmit={submit}>
      {mode === 'update' && (
        <fieldset className="inline">
          <legend>Decision</legend>
          {(['APPROVED', 'REJECTED'] as const).map((v) => (
            <label key={v}>
              <input type="radio" checked={decision === v} onChange={() => setDecision(v)} /> {v}
            </label>
          ))}
        </fieldset>
      )}
      <label className="field">
        Company type{approving ? ' (required)' : ' (optional)'}
        <select value={type} onChange={(e) => setType(e.target.value)}>
          <option value="" disabled={approving}>
            {approving ? 'Choose a reviewed type…' : 'Not recorded'}
          </option>
          <option value={UNKNOWN}>Unknown (keep unclassified, never a benchmark)</option>
          {COMPANY_TYPES.map((t) => (
            <option key={t} value={t}>{typeLabel(t)}</option>
          ))}
        </select>
      </label>
      {c.known_marketplace && approving && (
        <p className="small warn-text">Known marketplace: it can only be approved as “marketplace”.</p>
      )}
      <label className="field">
        Company name (optional, as shown on the site)
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      {approving && (
        <label className="field inline">
          <input
            type="checkbox"
            checked={canBenchmark && benchmark}
            disabled={!canBenchmark}
            onChange={(e) => setBenchmark(e.target.checked)}
          />{' '}
          Use as a direct retailer price benchmark
          {!canBenchmark && <span className="muted"> (retailer types only)</span>}
        </label>
      )}
      <label className="field">
        Notes (optional)
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} />
      </label>
      {!reviewer.trim() && <p className="small warn-text">Enter your reviewer name above first.</p>}
      {error && <p role="alert" className="error">{error}</p>}
      <button type="submit" disabled={!ready || busy}>
        {busy ? 'Saving…' : mode === 'update' ? 'Save change' : approving ? 'Approve' : 'Reject'}
      </button>{' '}
      <button type="button" className="link" onClick={onCancel}>Cancel</button>
    </form>
  )
}

function Details({ detail }: { detail: CandidateDetail }) {
  return (
    <div className="details">
      {detail.rationale && (
        <p className="small"><strong>LLM rationale:</strong> {detail.rationale}</p>
      )}
      <h4>Why it needs review (automatic rules)</h4>
      <ul className="small">
        {detail.automatic_reasons.map((r) => <li key={r}>{r}</li>)}
        {detail.validation_notes.map((r) => <li key={r}>{r}</li>)}
      </ul>

      <h4>Evidence ({detail.evidence.length})</h4>
      <ul className="evidence">
        {detail.evidence.map((e) => (
          <li key={`${e.kind}|${e.product_id}|${e.url}`}>
            <a href={e.url} target="_blank" rel="noreferrer noopener">{e.title || e.url}</a>
            <div className="small muted">
              {e.kind === 'domain_profile' ? 'Profile search' : `Discovery for ${e.product_id}`}
              {e.mentions_product_model && ' · model on page'} · query “{e.query_text}” ·{' '}
              {e.provider} · retrieved {when(e.retrieved_at)}
            </div>
            {e.snippet && <blockquote>{e.snippet}</blockquote>}
          </li>
        ))}
      </ul>

      <h4>Decision history</h4>
      {detail.history.length === 0 ? (
        <p className="small muted">No decisions yet.</p>
      ) : (
        <ol className="small">
          {detail.history.map((h) => (
            <li key={h.version}>
              v{h.version}: <strong>{h.decision}</strong> by {h.reviewer} on {when(h.decided_at)} · type{' '}
              {typeLabel(h.company_type)} · {h.benchmark_eligible ? 'benchmark' : 'not a benchmark'}
              {h.notes && <> · “{h.notes}”</>}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
