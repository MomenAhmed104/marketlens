// What the selected run searched and discovered. Approvals happen in the Pending
// section and apply to the next run; pending domains are never searched in this run.
// Each discovered domain also shows the backend's UK evidence view (Option B): the
// verified quotes and their kinds, unverified claims kept apart, and any contradiction.
import type { DiscoveredCompetitor, PipelineResult } from '../api'
import CompetitorClassPill from '../components/CompetitorClassPill'
import StatusPill from '../components/StatusPill'
import { UkDetails, UkSummary } from '../components/UkEvidence'
import { fromBackendClass } from '../competitorClass'
import { label } from '../format'
import { ukReviewOf } from '../ukRelevance'

export default function RunDomains({ result, onShowPending }: { result: PipelineResult; onShowPending: () => void }) {
  const disc = result.discovery
  const pending = result.new_pending_domains
  return (
    <div className="stack-sm">
      <p className="hint">
        {disc
          ? `Discovery (${disc.mode === 'LLM_ENABLED' ? 'LLM 1' : `no LLM: ${disc.llm_disabled_reason ?? 'disabled'}`}): ${
              disc.candidates.length
            } new candidate(s), ${disc.excluded.length} excluded. `
          : 'Discovery did not produce results for this run (not run, still running, or failed). '}
        {result.search_targets.length} domain(s) searched.
      </p>
      {pending.length > 0 && (
        <p className="callout warning">
          {pending.length} new domain(s) from this run are pending review and were <strong>not searched</strong>:{' '}
          {pending.join(', ')}.{' '}
          <button type="button" className="btn-link" onClick={onShowPending}>
            Review pending domains
          </button>
        </p>
      )}

      <details className="disclosure">
        <summary>Searched in this run ({result.search_targets.length})</summary>
        <div className="table-scroll">
          <table className="data">
            <thead>
              <tr>
                <th scope="col">Domain</th>
                <th scope="col">Company</th>
                <th scope="col">Type</th>
                <th scope="col">Approval</th>
                <th scope="col">Price benchmark</th>
              </tr>
            </thead>
            <tbody>
              {result.search_targets.map((t) => (
                <tr key={t.domain}>
                  <th scope="row">{t.domain}</th>
                  <td>{t.company_name ?? <span className="text-muted">unknown</span>}</td>
                  <td>{t.company_type ? label(t.company_type) : <span className="text-muted">unknown</span>}</td>
                  <td>
                    <StatusPill value={t.approval_status} />
                  </td>
                  <td>{t.price_benchmark ? 'Yes' : <span className="text-muted">No (searched, not compared)</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>

      {disc && disc.candidates.length > 0 && (
        <details className="disclosure">
          <summary>All domains discovered in this run ({disc.candidates.length})</summary>
          <DiscoveredDomainsTable candidates={disc.candidates} />
        </details>
      )}
    </div>
  )
}

function DiscoveredClassCell({ c }: { c: DiscoveredCompetitor }) {
  const cls = fromBackendClass(c.competitor_class)
  if (!cls) return <>{label(c.classification_status)}</>
  return (
    <>
      <CompetitorClassPill cls={cls} />
      <div className="cell-sub">{label(c.classification_status)}</div>
    </>
  )
}

// Compact status in the table; the quotes, missing evidence and reasons on demand.
function UkEvidenceCell({ c }: { c: DiscoveredCompetitor }) {
  const r = ukReviewOf(c)
  const quotes = r.verified_evidence.length + r.unverified_claims.length
  return (
    <>
      <UkSummary review={r} compact />
      {r.assessed && (
        <details className="disclosure uk-run-details">
          <summary>
            {quotes > 0 ? `UK evidence (${r.verified_evidence.length} verified, ${r.unverified_claims.length} unverified)` : 'How this was assessed'}
          </summary>
          <UkDetails review={r} showSummary />
        </details>
      )}
    </>
  )
}

/** One row per domain discovered in a run: approval in this run, type, class and UK evidence. */
export function DiscoveredDomainsTable({ candidates }: { candidates: DiscoveredCompetitor[] }) {
  return (
    <div className="table-scroll">
      <table className="data">
        <thead>
          <tr>
            <th scope="col">Domain</th>
            <th scope="col">Status in this run</th>
            <th scope="col">Company type</th>
            <th scope="col">Classification</th>
            <th scope="col">UK evidence</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((c) => (
            <tr key={c.domain}>
              <th scope="row">
                <a href={c.homepage_url} target="_blank" rel="noreferrer noopener">
                  {c.domain}
                </a>
              </th>
              <td>
                <StatusPill value={c.approval_status} />
                {c.review_decision && <div className="cell-sub">earlier reviewer decision</div>}
              </td>
              <td>{c.company_type ? label(c.company_type) : <span className="text-muted">unknown</span>}</td>
              <td>
                <DiscoveredClassCell c={c} />
              </td>
              <td>
                <UkEvidenceCell c={c} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
