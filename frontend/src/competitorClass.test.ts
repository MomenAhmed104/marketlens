import { describe, expect, it } from 'vitest'
import type { BackendCompetitorClass, CandidateSummary, CompanyType } from './api'
import {
  COMPETITOR_CLASS_LABEL,
  competitorClass,
  effectiveCompanyType,
  fromBackendClass,
  resolveCompetitorClass,
} from './competitorClass'
import { makeCandidate } from './test/fakes'

// An LLM-classified record with every score that the old frontend rule treated as "Direct".
const llm = (over: Partial<CandidateSummary> = {}): CandidateSummary => ({
  ...makeCandidate('shop.example'),
  discovery_mode: 'LLM_ENABLED',
  classification_status: 'COMPLETED',
  llm_classified: true,
  proposed_company_type: 'retailer',
  relevance: 0.9,
  confidence: 0.95,
  serves_uk_customers: 'yes',
  ...over,
})

const decided = (over: Partial<NonNullable<CandidateSummary['decision']>>, cand: Partial<CandidateSummary> = {}) => {
  const base = makeCandidate('shop.example', 'APPROVED')
  return { ...base, ...cand, decision: { ...base.decision!, ...over } }
}

describe('competitorClass: backend class is the source of truth', () => {
  it('uses exactly the three labels', () => {
    expect(Object.values(COMPETITOR_CLASS_LABEL)).toEqual(['Direct Competitor', 'Related Retailer', 'Needs Review'])
  })

  it.each<[BackendCompetitorClass, string]>([
    ['DIRECT_COMPETITOR', 'DIRECT'],
    ['RELATED_RETAILER', 'RELATED'],
    ['NEEDS_REVIEW', 'NEEDS_REVIEW'],
  ])('maps backend %s', (backend, shown) => {
    expect(resolveCompetitorClass({ ...makeCandidate('x.example'), competitor_class: backend })).toEqual({
      cls: shown,
      source: 'backend',
    })
  })

  it('takes precedence over everything the fallback would use', () => {
    // fallback would say Related (known marketplace); backend says Needs Review
    expect(competitorClass({ ...makeCandidate('ebay.fr'), known_marketplace: true, competitor_class: 'NEEDS_REVIEW' })).toBe(
      'NEEDS_REVIEW',
    )
    // fallback would say Needs Review (no LLM data); backend says Direct
    expect(competitorClass({ ...makeCandidate('nollm.example'), competitor_class: 'DIRECT_COMPETITOR' })).toBe('DIRECT')
    // high scores do not override a backend Related or Needs Review
    expect(competitorClass(llm({ relevance: 1, competitor_class: 'RELATED_RETAILER' }))).toBe('RELATED')
    expect(competitorClass(llm({ relevance: 1, competitor_class: 'NEEDS_REVIEW' }))).toBe('NEEDS_REVIEW')
    // the proposal is informational only: the final class is shown
    expect(competitorClass(llm({ proposed_competitor_class: 'DIRECT_COMPETITOR', competitor_class: 'NEEDS_REVIEW' }))).toBe(
      'NEEDS_REVIEW',
    )
  })

  it('ignores unknown backend values instead of guessing', () => {
    expect(fromBackendClass('DIRECT')).toBeNull()
    expect(fromBackendClass('toString')).toBeNull()
    expect(fromBackendClass(undefined)).toBeNull()
    const odd = { ...llm(), competitor_class: 'SOMETHING_NEW' as BackendCompetitorClass }
    expect(resolveCompetitorClass(odd)).toEqual({ cls: 'NEEDS_REVIEW', source: 'fallback' })
  })
})

describe('competitorClass: temporary fallback for records without a backend class', () => {
  it.each([undefined, null])('is used when competitor_class is %s', (value) => {
    expect(resolveCompetitorClass({ ...makeCandidate('old.example'), competitor_class: value }).source).toBe('fallback')
  })

  it('never makes a retailer Direct from a relevance score alone', () => {
    for (const relevance of [0.75, 0.9, 1]) {
      expect(competitorClass(llm({ relevance }))).toBe('NEEDS_REVIEW')
      expect(competitorClass(llm({ relevance, proposed_company_type: 'specialist_retailer' }))).toBe('NEEDS_REVIEW')
    }
  })

  it('never gives Direct Competitor, whatever else the record says', () => {
    const cases: CandidateSummary[] = [
      llm(),
      llm({ proposed_competitor_class: 'DIRECT_COMPETITOR' }), // a proposal is not a final class
      decided({ company_type: 'retailer', benchmark_eligible: true }),
      decided({ company_type: 'specialist_retailer', benchmark_eligible: true }, llm()),
    ]
    for (const c of cases) expect(competitorClass(c)).not.toBe('DIRECT')
  })

  it.each<CompanyType>(['manufacturer', 'directory', 'other'])('%s is Needs Review', (type) => {
    expect(competitorClass(llm({ proposed_company_type: type }))).toBe('NEEDS_REVIEW')
    expect(competitorClass(decided({ company_type: type }))).toBe('NEEDS_REVIEW')
  })

  it('known marketplaces are Related Retailer', () => {
    expect(competitorClass({ ...makeCandidate('amazon.co.uk'), known_marketplace: true })).toBe('RELATED')
    expect(competitorClass(llm({ domain: 'amazon.co.uk', known_marketplace: true, relevance: 0.99 }))).toBe('RELATED')
    expect(competitorClass(decided({ company_type: 'marketplace' }))).toBe('RELATED') // reviewer-typed marketplace
  })

  it('uncertain or missing data is Needs Review', () => {
    expect(competitorClass(makeCandidate('nollm.example'))).toBe('NEEDS_REVIEW')
    expect(competitorClass(llm({ proposed_company_type: null }))).toBe('NEEDS_REVIEW')
    expect(competitorClass(llm({ serves_uk_customers: 'unknown', relevance: null }))).toBe('NEEDS_REVIEW')
    expect(competitorClass(llm({ proposed_company_type: 'marketplace' }))).toBe('NEEDS_REVIEW') // unreviewed LLM type only
  })
})

describe('effectiveCompanyType', () => {
  it('uses the reviewer decision type over the LLM proposal', () => {
    expect(effectiveCompanyType(decided({ company_type: 'marketplace' }, { proposed_company_type: 'retailer' }))).toBe(
      'marketplace',
    )
    expect(effectiveCompanyType(llm())).toBe('retailer')
    expect(effectiveCompanyType(llm({ llm_classified: false }))).toBeNull()
  })
})
