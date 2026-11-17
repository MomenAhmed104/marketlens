// Competitor classification shown on the Domain Review page and in live run views.
//
// The backend's `competitor_class` is the source of truth: LLM 1 proposes a class and
// deterministic backend rules (`rules.classify_competitor`) apply the safeguards,
// including "a known marketplace is always Related Retailer". The frontend only maps
// it to a label.
//
// Temporary fallback, for records stored before the backend classified them (not yet
// backfilled): a known marketplace domain, or a domain a reviewer typed as a
// marketplace, is Related Retailer; everything else is Needs Review. The fallback never
// gives Direct Competitor, and relevance or confidence scores are never used.
import type { BackendCompetitorClass, CandidateSummary, CompanyType } from './api'

export type CompetitorClass = 'DIRECT' | 'RELATED' | 'NEEDS_REVIEW'
/** Where a displayed class came from: the backend field, or the temporary fallback. */
export type ClassSource = 'backend' | 'fallback'

export const COMPETITOR_CLASS_LABEL: Record<CompetitorClass, string> = {
  DIRECT: 'Direct Competitor',
  RELATED: 'Related Retailer',
  NEEDS_REVIEW: 'Needs Review',
}

const FROM_BACKEND: Record<BackendCompetitorClass, CompetitorClass> = {
  DIRECT_COMPETITOR: 'DIRECT',
  RELATED_RETAILER: 'RELATED',
  NEEDS_REVIEW: 'NEEDS_REVIEW',
}

/** The backend value as a display class; null if missing or not a known value. */
export function fromBackendClass(value: unknown): CompetitorClass | null {
  return typeof value === 'string' && Object.hasOwn(FROM_BACKEND, value)
    ? FROM_BACKEND[value as BackendCompetitorClass]
    : null
}

/** The company type in force: the reviewer's, else LLM 1's proposal (only if LLM-classified). */
export function effectiveCompanyType(c: CandidateSummary): CompanyType | null {
  if (c.decision) return c.decision.company_type
  return c.llm_classified ? c.proposed_company_type : null
}

/** Temporary fallback for records without a backend class. Never Direct Competitor. */
function fallbackClass(c: CandidateSummary): CompetitorClass {
  if (c.known_marketplace || c.decision?.company_type === 'marketplace') return 'RELATED'
  return 'NEEDS_REVIEW' // retailers, manufacturers, directories, other and unknown types
}

export function resolveCompetitorClass(c: CandidateSummary): { cls: CompetitorClass; source: ClassSource } {
  const backend = fromBackendClass(c.competitor_class)
  return backend ? { cls: backend, source: 'backend' } : { cls: fallbackClass(c), source: 'fallback' }
}

export function competitorClass(c: CandidateSummary): CompetitorClass {
  return resolveCompetitorClass(c).cls
}
