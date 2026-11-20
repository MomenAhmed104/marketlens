// Plain-English labels for the backend's UK relevance evidence view (Option B).
//
// There is no UK category. The backend verifies LLM 1's quotes and decides whether the
// evidence is sufficient, contradicted or mixed (rules.uk_review); this module only names
// things for people. UK evidence never approves a domain and never grants benchmark permission.
import type { Tone } from './status'
import type { CandidateSummary, CompetitorDiscoveredPayload, UkEvidenceKind, UkReview } from './api'

export const UK_EVIDENCE_KIND_LABEL: Record<UkEvidenceKind, string> = {
  UK_PHYSICAL_STORE: 'UK shop or showroom',
  UK_ADDRESS: 'UK address',
  UK_COMPANY_REGISTRATION: 'UK company registration',
  UK_DELIVERY: 'Delivers to the UK',
  NON_UK_OPERATOR: 'Based outside the UK',
  NO_UK_SERVICE: 'Does not serve the UK',
  WEAK_SIGNAL: 'Weak signal (GBP, .uk or “UK” only)',
}

/** Shown for records without a UK review (older backends): never treated as verified. */
const NOT_ASSESSED: UkReview = {
  assessed: false,
  sufficient: false,
  contradicted: false,
  mixed_location: false,
  independent_sources: 0,
  summary: 'Not assessed: there is no UK evidence assessment for this domain. Check the evidence and decide.',
  reasons: [],
  missing_evidence: [],
  verified_evidence: [],
  unverified_claims: [],
}

/** Works for review candidates and for discovery candidates in a run result. */
export function ukReviewOf(c: Pick<CandidateSummary, 'uk_review'>): UkReview {
  return c.uk_review ?? NOT_ASSESSED
}

/** The headline for the card: what the verified evidence shows, and how sure it is. */
export function ukEvidenceStatus(r: UkReview): { label: string; tone: Tone } {
  if (!r.assessed) return { label: 'UK evidence not assessed', tone: 'warning' }
  if (r.contradicted) return { label: 'Contradictory UK evidence', tone: 'danger' }
  if (r.sufficient && r.mixed_location) return { label: 'UK evidence: check location', tone: 'warning' }
  if (r.sufficient) return { label: 'Verified UK evidence', tone: 'success' }
  return { label: 'Insufficient UK evidence', tone: 'warning' }
}

/**
 * The same headline from a `competitor_discovered` event, which carries only the flags (the
 * quotes are in the run result). Null for events from older backends without UK fields: nothing
 * is shown rather than a guess.
 */
export function ukEventStatus(p: Partial<CompetitorDiscoveredPayload>): { label: string; tone: Tone; line: string } | null {
  if (p.uk_assessed === undefined) return null
  const r: UkReview = {
    ...NOT_ASSESSED,
    assessed: p.uk_assessed,
    sufficient: p.uk_evidence_sufficient ?? false,
    contradicted: p.uk_evidence_contradicted ?? false,
    mixed_location: p.uk_mixed_location ?? false,
    independent_sources: p.uk_independent_sources ?? 0,
  }
  return { ...ukEvidenceStatus(r), line: ukEvidenceLine(r) }
}

/** One short line for the card, e.g. "From 2 independent pages". */
export function ukEvidenceLine(r: UkReview): string {
  if (!r.assessed) return 'No verified UK evidence yet'
  const n = r.independent_sources
  if (n === 0) return 'No supporting page verified'
  return `From ${n} independent page${n === 1 ? '' : 's'}`
}
