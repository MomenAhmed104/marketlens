// Phase 4: competitor domain review. Discovered domains are reviewed here;
// decisions persist on the backend. Not the Phase 8 run dashboard.
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import Avatar from './components/Avatar'
import StatusPill from './components/StatusPill'
import CompetitorClassPill from './components/CompetitorClassPill'
import { effectiveCompanyType, resolveCompetitorClass } from './competitorClass'
import { useReviewer } from './reviewer'
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

const typeLabel = (t: CompanyType | null) => (t ? t.replace('_', ' ') : 'Unknown type')
const when = (iso: string) => new Date(iso).toLocaleString()
const day = (iso: string) => new Date(iso).toLocaleDateString()

interface ReviewProps {
  /** When set, the list is fixed to this status and the page supplies heading and tabs (Domain Review page). */
  status?: ReviewStatus
  /** Called after any decision or discovery run, so the app can refresh counts. */
  onChanged?: () => void
}

export default function Review({ status, onChanged }: ReviewProps = {}) {
  const [candidates, setCandidates] = useState<CandidateSummary[] | null>(null)
  const [ownFilter, setFilter] = useState<Filter>('PENDING')
  const filter: Filter = status ?? ownFilter
  const embedded = status !== undefined
  const [error, setError] = useState<string | null>(null)
  const [reviewer, setReviewer] = useReviewer()

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
  const changed = () => {
    reload()
    onChanged?.()
  }

  const counts = (f: Filter) =>
    candidates?.filter((c) => f === 'ALL' || c.review_status === f).length ?? 0
  const shown = candidates?.filter((c) => filter === 'ALL' || c.review_status === filter) ?? []

  return (
    <section aria-labelledby={embedded ? undefined : 'review-heading'} aria-label={embedded ? 'Review queue' : undefined}>
      {!embedded && (
        <>
          <h2 id="review-heading">Competitor domain review</h2>
          <p className="muted">
            Discovered companies need a reviewer decision before they can be used in targeted
            product searches. The 13 configured competitors are not listed here and stay unchanged.
          </p>
        </>
      )}

      {(!embedded || status === 'PENDING') && <DiscoveryRunner onDone={changed} />}

      <label className="field inline">
        Reviewer name{' '}
        <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="Your name" />
      </label>

      {!embedded && (
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
      )}

      {error && <p role="alert" className="error">{error}</p>}
      {!candidates && !error && <p>Loading review queue…</p>}
      {candidates && shown.length === 0 && (
        <p className="muted">
          {candidates.length === 0
            ? 'No discovered companies yet. Run discovery for one or more products above.'
            : 'Nothing in this list.'}
        </p>
      )}
      <ul className="domain-cards">
        {shown.map((c) => (
          <CandidateCard
            key={c.domain}
            candidate={c}
            reviewer={reviewer}
            onChanged={changed}
          />
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
    <form className="discovery-runner" onSubmit={submit} aria-label="Run discovery">
      <label className="field inline">
        Run discovery for product IDs{' '}
        <input value={ids} onChange={(e) => setIds(e.target.value)} placeholder="SB-001, SB-044" />
      </label>{' '}
      <button type="submit" className="btn" disabled={busy}>
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

export function CandidateCard(props: {
  candidate: CandidateSummary
  reviewer: string
  onChanged: () => void
}) {
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
  const shownType = effectiveCompanyType(c)
  // Backend class (source of truth); a provisional fallback only for unclassified older records.
  const { cls, source: clsSource } = resolveCompetitorClass(c)
  const domainLink = (className: string) => (
    <a href={c.homepage_url} target="_blank" rel="noreferrer noopener" className={className}>{c.domain}</a>
  )

  return (
    <li className={`domain-card state-${c.review_status.toLowerCase()}`}>
      <div className="domain-card-head">
        <Avatar name={name ?? c.domain} />
        {/* No verified company name: the domain itself is the title. */}
        <div className="domain-id">
          {name ? (
            <>
              <span className="domain-name">{name}</span>
              {domainLink('cell-mono')}
            </>
          ) : (
            domainLink('domain-name domain-title')
          )}
        </div>
        <StatusPill value={c.review_status} kind="review" />
      </div>

      <div className="domain-flags">
        <CompetitorClassPill cls={cls} source={clsSource} />
        {/* No type: the classification ("Needs Review") already says so. */}
        {shownType ? (
          <span className="tag" title="Company type">
            {typeLabel(shownType)}
            {!d && ' (LLM proposal)'}
          </span>
        ) : (
          c.known_marketplace && (
            <span className="tag" title="Company type">marketplace (known marketplace domain)</span>
          )
        )}
        {d && (
          <span className="decision-meta">
            {d.decision === 'APPROVED' ? 'Approved' : 'Rejected'} by {d.reviewer}, {day(d.decided_at)}
          </span>
        )}
      </div>

      <div className="domain-actions">
        {c.review_status === 'PENDING' ? (
          <>
            <button className="btn primary" aria-expanded={mode === 'approve'} onClick={() => setMode(mode === 'approve' ? null : 'approve')}>Approve…</button>
            <button className="btn" aria-expanded={mode === 'reject'} onClick={() => setMode(mode === 'reject' ? null : 'reject')}>Reject…</button>
          </>
        ) : (
          <button className="btn" aria-expanded={mode === 'update'} onClick={() => setMode(mode === 'update' ? null : 'update')}>Change decision…</button>
        )}
        <button className="btn-link push-right" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? 'Hide evidence' : `Show evidence (${c.evidence_count})`}
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
    <form className="decision" onSubmit={submit} aria-label="Decision">
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
      <button type="submit" className="btn primary" disabled={!ready || busy}>
        {busy ? 'Saving…' : mode === 'update' ? 'Save change' : approving ? 'Approve' : 'Reject'}
      </button>{' '}
      <button type="button" className="btn-link" onClick={onCancel}>Cancel</button>
    </form>
  )
}

// Evidence only: product pages first (they show the product on sale), then profile pages.
function Details({ detail }: { detail: CandidateDetail }) {
  const evidence = [...detail.evidence].sort(
    (a, b) => Number(b.kind === 'discovery_result') - Number(a.kind === 'discovery_result'),
  )
  return (
    <div className="details">
      {evidence.length === 0 && <p className="small muted">No evidence stored.</p>}
      <ul className="evidence" aria-label="Evidence">
        {evidence.map((e) => (
          <li key={`${e.kind}|${e.product_id}|${e.url}`} className="evidence-item">
            <a href={e.url} target="_blank" rel="noreferrer noopener" className="evidence-title">{e.title || e.url}</a>
            {e.snippet && <blockquote>{e.snippet}</blockquote>}
            <div className="evidence-meta">
              {e.kind === 'domain_profile' ? 'Company profile' : `Product page for ${e.product_id}`} · retrieved{' '}
              {when(e.retrieved_at)}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
