// Plain-English search status of a discovered domain (Stage 8.1).
//
// The backend decides whether a domain is searched (`search_basis`, from the reviewer's decision
// or the deterministic rules.is_search_eligible) and says why (`search_eligibility_reasons`);
// this module only names that for people. There is no UK verification: LLM 1's
// `serves_uk_customers` answer is shown as information only. Search status never implies
// benchmark permission, which is a separate reviewer flag.
import { CLASSIFICATION_VERSION, type CandidateSummary, type CompanyType, type SearchBasis, type ServesUkCustomers } from './api'
import type { Tone } from './status'

export type SearchStatusKind =
  | 'AUTOMATIC' // automatically searchable retailer
  | 'MANUAL_APPROVAL' // approved by a reviewer
  | 'REJECTED'
  | 'AUTO_OFF' // eligible, but automatic search is switched off
  | 'MANUFACTURER'
  | 'MARKETPLACE'
  | 'DIRECTORY'
  | 'USED_ONLY' // a retailer the evidence shows sells only used instruments
  | 'UNCLASSIFIED' // no completed LLM 1 classification, or one from before Stage 8.1
  | 'PENDING' // classified, but a basic check failed

export interface SearchStatus {
  kind: SearchStatusKind
  label: string
  tone: Tone
  /** Why, in one sentence (from the backend's reasons when it gave them). */
  line: string
  reasons: string[]
}

type Input = Pick<
  CandidateSummary,
  'review_status' | 'llm_classified' | 'proposed_company_type' | 'known_marketplace' | 'decision'
> &
  Partial<
    Pick<
      CandidateSummary,
      'search_basis' | 'auto_search_eligible' | 'search_eligibility_reasons' | 'classification_version' | 'sells_new_musical_instruments'
    >
  >

/** Older backends do not send `search_basis`: derive it from the review status only. */
function basisOf(c: Input): SearchBasis {
  if (c.search_basis) return c.search_basis
  return c.review_status === 'APPROVED' ? 'MANUAL_APPROVAL' : 'NOT_SEARCHED'
}

const strip = (r: string) => r.replace(/^Not searched until approved: /, '')

export function searchStatusOf(c: Input): SearchStatus {
  const reasons = c.search_eligibility_reasons ?? []
  const first = reasons[0] ? strip(reasons[0]) : ''
  const make = (kind: SearchStatusKind, label: string, tone: Tone, line: string): SearchStatus => ({
    kind, label, tone, line, reasons,
  })
  const basis = basisOf(c)
  if (c.review_status === 'REJECTED') return make('REJECTED', 'Rejected: never searched', 'danger', first || 'Rejected by a reviewer')
  if (basis === 'MANUAL_APPROVAL') return make('MANUAL_APPROVAL', 'Manually approved: searched', 'success', first || 'Approved by a reviewer')
  if (basis === 'AUTOMATIC') {
    return make('AUTOMATIC', 'Automatically searchable retailer', 'success',
      'A retailer named in the discovery evidence, with cited evidence. Searched without approval (capped per run); reject it to stop.')
  }
  if (c.auto_search_eligible) {
    return make('AUTO_OFF', 'Eligible: automatic search is off', 'warning', 'It would be searched automatically, but automatic search is switched off. Approve it to search it.')
  }
  const type: CompanyType | null = c.decision?.company_type ?? c.proposed_company_type
  if (c.known_marketplace || type === 'marketplace') {
    return make('MARKETPLACE', 'Marketplace: needs review', 'warning', 'Marketplaces are never searched automatically. Approve it as a marketplace to search it.')
  }
  if (type === 'directory') {
    return make('DIRECTORY', 'Directory: not searched for offers', 'neutral', 'Lists other sellers, so it is not searched for offers even when approved.')
  }
  if (type === 'manufacturer') {
    return make('MANUFACTURER', 'Manufacturer: needs review', 'warning', 'Manufacturer sites are never searched automatically. A reviewer decides.')
  }
  if (!c.llm_classified || c.classification_version !== CLASSIFICATION_VERSION) {
    return make('UNCLASSIFIED', 'Pending: not classified yet', 'warning',
      c.llm_classified
        ? 'Classified before the current discovery prompt: it needs rediscovery (or a reviewer decision) before it can be searched.'
        : 'LLM 1 has not classified it: a reviewer decides, or a later discovery run classifies it.')
  }
  if (c.sells_new_musical_instruments === false) {
    return make('USED_ONLY', 'Used-only retailer: needs review', 'warning',
      'The evidence shows it sells only used instruments, so it is not searched automatically. A reviewer can approve it; that does not make it a price benchmark.')
  }
  return make('PENDING', 'Pending review', 'warning', first ? `Not searched yet: ${first}` : 'Not searched until approved.')
}

export const SERVES_UK_LABEL: Record<ServesUkCustomers, string> = {
  yes: 'LLM 1 says it sells to UK customers',
  no: 'LLM 1 says it does not sell to UK customers',
  unknown: 'LLM 1 could not tell whether it sells to UK customers',
}
