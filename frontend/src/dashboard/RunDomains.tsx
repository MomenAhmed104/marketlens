// What the selected run searched and discovered. Approved and automatically eligible
// domains can be searched; pending and rejected domains cannot. Each discovered domain shows
// whether this run actually searched it (from the run's search records) and the backend's
// reasons for its status. There is no UK verification.
import type { DiscoveredCompetitor, DomainRunOutcome, PipelineResult } from '../api'
import CompetitorClassPill from '../components/CompetitorClassPill'
import StatusPill from '../components/StatusPill'
import { fromBackendClass } from '../competitorClass'
import { label } from '../format'
import { explain } from '../plainLanguage'
import { domainOutcomes, eligibleNotSearched, runDomainStatus, searchedDomains } from '../domainStatus'
import { SERVES_UK_LABEL } from '../searchEligibility'

export default function RunDomains({ result, onShowPending }: { result: PipelineResult; onShowPending: () => void }) {
  const disc = result.discovery
  const pending = result.new_pending_domains
  const notSearched = eligibleNotSearched(result)
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
      {notSearched.length > 0 && (
        <div className="callout info" data-testid="eligible-not-searched">
          <p>
            {notSearched.length} discovered domain(s) were approved or automatically eligible but were{' '}
            <strong>not searched</strong> in this run:
          </p>
          <ul className="small">
            {notSearched.map((o) => (
              <li key={o.domain}>
                {o.domain}: {o.reason ? explain(o.reason) : 'reason not recorded'}
              </li>
            ))}
          </ul>
        </div>
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
          <DiscoveredDomainsTable
            candidates={disc.candidates}
            searched={searchedDomains(result)}
            outcomes={domainOutcomes(result)}
          />
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

// The domain's status in this run, with the backend's reasons on demand.
function ReasonsCell({
  c,
  searched,
  outcome,
}: {
  c: DiscoveredCompetitor
  searched: ReadonlySet<string>
  outcome?: DomainRunOutcome
}) {
  const reasons = c.decision_reasons ?? []
  const status = runDomainStatus(c, searched)
  // The backend's reason this domain was not a target (e.g. automatic search switched off, the cap).
  const why = outcome?.reason && status !== 'Searched in this run' && status !== 'Pending review' ? outcome.reason : null
  return (
    <>
      <span className="small" data-testid="run-domain-status">{status}</span>
      {why && <div className="cell-sub" data-testid="run-domain-reason">{explain(why)}</div>}
      {(reasons.length > 0 || c.serves_uk_customers) && (
        <details className="disclosure run-reasons">
          <summary>Why</summary>
          <ul className="small">{reasons.map((r) => <li key={r}>{explain(r)}</li>)}</ul>
          {c.serves_uk_customers && (
            <p className="small muted">{SERVES_UK_LABEL[c.serves_uk_customers]} (information only).</p>
          )}
        </details>
      )}
    </>
  )
}

/** One row per domain discovered in a run: status in this run, type, class and why. */
export function DiscoveredDomainsTable({
  candidates,
  searched,
  outcomes = new Map(),
}: {
  candidates: DiscoveredCompetitor[]
  searched: ReadonlySet<string>
  outcomes?: ReadonlyMap<string, DomainRunOutcome>
}) {
  return (
    <div className="table-scroll">
      <table className="data">
        <thead>
          <tr>
            <th scope="col">Domain</th>
            <th scope="col">Status in this run</th>
            <th scope="col">Company type</th>
            <th scope="col">Classification</th>
            <th scope="col">Why</th>
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
                <ReasonsCell c={c} searched={searched} outcome={outcomes.get(c.domain)} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
