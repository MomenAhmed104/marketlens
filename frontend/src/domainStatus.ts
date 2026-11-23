import type { DiscoveredCompetitor, DomainRunOutcome, PipelineResult } from './api'

/**
 * Domains this run actually searched, from the run's own search records: every domain that a
 * product search queried (`products[].intelligence.domains`), whether or not it found pages.
 * Approval alone never counts as searched.
 */
export function searchedDomains(result: PipelineResult | null | undefined): ReadonlySet<string> {
  const out = new Set<string>()
  for (const p of result?.products ?? []) {
    for (const d of p.intelligence?.domains ?? []) {
      if (d.queries.length > 0) out.add(d.domain)
    }
  }
  return out
}

/**
 * What happened to a discovered domain in this run. A rejection always wins; then whether the
 * run's search records show the domain was actually searched; then why it was not.
 */
export function runDomainStatus(
  c: Pick<DiscoveredCompetitor, 'domain' | 'approval_status' | 'review_decision'>,
  searched: ReadonlySet<string> = new Set(),
): string {
  if (c.review_decision?.decision === 'REJECTED' || c.approval_status === 'REJECTED') return 'Rejected — never searched'
  if (c.approval_status === 'PRECONFIGURED') return 'Configured competitor'
  if (searched.has(c.domain)) return 'Searched in this run'
  if (c.review_decision?.decision === 'APPROVED' || c.approval_status === 'APPROVED') return 'Approved — not searched in this run'
  if (c.approval_status === 'AUTO_APPROVED') return 'Automatically eligible — not searched in this run'
  return 'Pending review'
}

/**
 * The backend's per-domain outcome for this run (Stage 8.2), keyed by domain. Empty for runs saved
 * before it existed, so callers fall back to `runDomainStatus` alone.
 */
export function domainOutcomes(result: PipelineResult | null | undefined): ReadonlyMap<string, DomainRunOutcome> {
  return new Map((result?.domain_outcomes ?? []).map((o) => [o.domain, o]))
}

/** Domains that passed the automatic checks or were approved, yet were not targets of this run. */
export function eligibleNotSearched(result: PipelineResult | null | undefined): DomainRunOutcome[] {
  return (result?.domain_outcomes ?? []).filter(
    (o) => o.status === 'AUTO_ELIGIBLE_NOT_SEARCHED' || o.status === 'APPROVED_NOT_SEARCHED',
  )
}
