import { describe, expect, it } from 'vitest'
import type { CandidateSummary } from './api'
import { searchStatusOf } from './searchEligibility'
import { makeCandidate } from './test/fakes'

const c = (over: Partial<CandidateSummary> = {}, status: CandidateSummary['review_status'] = 'PENDING'): CandidateSummary => ({
  ...makeCandidate('shop.example', status),
  llm_classified: true,
  classification_status: 'COMPLETED',
  classification_version: 3,
  proposed_company_type: 'retailer',
  ...over,
})

describe('searchStatusOf', () => {
  it('shows an automatically searchable retailer from the backend basis', () => {
    const st = searchStatusOf(c({ search_basis: 'AUTOMATIC', auto_search_eligible: true, search_eligible: true }))
    expect(st.kind).toBe('AUTOMATIC')
    expect(st.label).toBe('Automatically searchable retailer')
  })

  it('distinguishes manual approval and rejection, whatever automatic eligibility says', () => {
    expect(searchStatusOf(c({}, 'APPROVED')).kind).toBe('MANUAL_APPROVAL')
    const rejected = c({ search_basis: 'NOT_SEARCHED', auto_search_eligible: false }, 'REJECTED')
    expect(searchStatusOf(rejected).kind).toBe('REJECTED')
    expect(searchStatusOf(rejected).label).toBe('Rejected: never searched')
  })

  it('names manufacturers, marketplaces and directories', () => {
    expect(searchStatusOf(c({ proposed_company_type: 'manufacturer' })).kind).toBe('MANUFACTURER')
    expect(searchStatusOf(c({ proposed_company_type: 'marketplace' })).kind).toBe('MARKETPLACE')
    expect(searchStatusOf(c({ proposed_company_type: 'retailer', known_marketplace: true })).kind).toBe('MARKETPLACE')
    expect(searchStatusOf(c({ proposed_company_type: 'directory' })).kind).toBe('DIRECTORY')
  })

  it('pending: unclassified, classified before the current prompt, or failing a basic check', () => {
    expect(searchStatusOf(makeCandidate('nollm.example')).kind).toBe('UNCLASSIFIED')
    const old = searchStatusOf(c({ classification_version: null }))
    expect(old.kind).toBe('UNCLASSIFIED')
    expect(old.line).toMatch(/needs rediscovery/)
    expect(searchStatusOf(c({ classification_version: 2 })).kind).toBe('UNCLASSIFIED') // an older Stage 8.1 version
    const failing = searchStatusOf(c({
      search_basis: 'NOT_SEARCHED',
      search_eligibility_reasons: ['Not searched until approved: company name not found in the discovery evidence'],
    }))
    expect(failing.kind).toBe('PENDING')
    expect(failing.line).toBe('Not searched yet: company name not found in the discovery evidence')
  })

  it('used-only retailers need review; new-and-used or unknown status does not change the status', () => {
    const used = searchStatusOf(c({ search_basis: 'NOT_SEARCHED', sells_new_musical_instruments: false }))
    expect(used.kind).toBe('USED_ONLY')
    expect(used.label).toBe('Used-only retailer: needs review')
    expect(used.line).toMatch(/does not make it a price benchmark/)
    expect(searchStatusOf(c({ search_basis: 'AUTOMATIC', sells_new_musical_instruments: true })).kind).toBe('AUTOMATIC')
    expect(searchStatusOf(c({ search_basis: 'AUTOMATIC', sells_new_musical_instruments: null })).kind).toBe('AUTOMATIC')
    expect(searchStatusOf(c({ search_basis: 'NOT_SEARCHED', sells_new_musical_instruments: null })).kind).toBe('PENDING')
    // Approved used-only retailers show as manually approved.
    expect(searchStatusOf(c({ sells_new_musical_instruments: false }, 'APPROVED')).kind).toBe('MANUAL_APPROVAL')
  })

  it('eligible while automatic search is switched off', () => {
    const st = searchStatusOf(c({ search_basis: 'NOT_SEARCHED', auto_search_eligible: true }))
    expect(st.kind).toBe('AUTO_OFF')
  })

  it('never uses the informational UK answer', () => {
    for (const answer of ['yes', 'no', 'unknown'] as const) {
      expect(searchStatusOf(c({ search_basis: 'AUTOMATIC', serves_uk_customers: answer })).kind).toBe('AUTOMATIC')
    }
  })

  it('older backends without search_basis fall back on the review status only', () => {
    const { search_basis: _b, ...older } = c()
    expect(searchStatusOf(older).kind).toBe('PENDING')
    expect(searchStatusOf({ ...older, review_status: 'APPROVED' }).kind).toBe('MANUAL_APPROVAL')
  })
})
