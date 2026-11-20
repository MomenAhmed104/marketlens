import { describe, expect, it } from 'vitest'
import { coverageRows, eligibleComparisons, keyFinding, overviewKpis, priceGate, productInsights } from './selectors'
import { fixture } from './test/fakes'

describe('overview selectors (backend-generated fixture)', () => {
  it('takes KPIs straight from backend fields', () => {
    const { run, result } = fixture()
    expect(overviewKpis(run, result)).toEqual({
      productsCompleted: run.summary.products_completed,
      productsTotal: run.summary.products_total,
      productsFailed: 0,
      competitorsSearched: result.search_targets.length,
      searchedByApproval: { PRECONFIGURED: 13, AUTO_APPROVED: 1 },
      exactMatches: run.summary.exact_matches,
      comparablePrices: run.summary.comparable_prices,
      productsNeedingReview: 1,
      reviewCandidates: run.summary.review_required,
    })
    expect(overviewKpis(run, result)).toMatchObject({ productsCompleted: 1, competitorsSearched: 14, exactMatches: 3, comparablePrices: 1 })
  })

  it('lists only eligible comparisons, with the backend difference', () => {
    const { result } = fixture()
    const rows = eligibleComparisons(result)
    expect(rows).toHaveLength(1)
    expect(rows[0].candidate.competitor.domain).toBe('andertons.co.uk')
    expect(rows[0].candidate.comparison.difference_gbp).toBe('-0.99')
    expect(rows.every((r) => r.candidate.comparison.eligible)).toBe(true)

    // An ineligible candidate is never listed, even if it carries a difference.
    const gear = result.products[0].matching!.candidates.find((c) => c.competitor.domain === 'gear4music.com')!
    gear.comparison = { ...gear.comparison, eligible: false, difference_gbp: '5.00' }
    expect(eligibleComparisons(result)).toHaveLength(1)
  })

  it('counts candidates per searched domain and match status', () => {
    const { result } = fixture()
    const rows = coverageRows(result)
    expect(rows).toHaveLength(14)
    const and = rows.find((r) => r.target.domain === 'andertons.co.uk')!
    expect(and.counts).toEqual({ EXACT_MATCH: 1, EQUIVALENT_VARIANT: 0, REVIEW_REQUIRED: 0, SIMILAR_MATCH: 1, NO_MATCH: 0 })
    expect(rows.find((r) => r.target.domain === 'gibson.com')!.counts.REVIEW_REQUIRED).toBe(1)
    expect(rows.filter((r) => r.candidates === 0).length).toBe(14 - 4)
    expect(coverageRows(null)).toEqual([])
  })

  it('groups products without an exact match', () => {
    const { result } = fixture()
    expect(productInsights(result)).toEqual({ noCandidates: [], noExactMatch: [], failed: [] })

    const m = result.products[0].matching!
    m.counts.EXACT_MATCH = 0
    expect(productInsights(result).noExactMatch.map((p) => p.product_id)).toEqual(['SB-001'])
    m.candidates = []
    expect(productInsights(result).noCandidates.map((p) => p.product_id)).toEqual(['SB-001'])
    result.products.push({ product_id: 'SB-002', status: 'FAILED', error: 'Timed out', intelligence: null, matching: null })
    expect(productInsights(result).failed.map((p) => p.product_id)).toEqual(['SB-002'])
  })

  it('picks the hero finding by fixed rules', () => {
    const { run, result } = fixture()
    expect(keyFinding(run, result)).toEqual({
      headline: '3 exact matches across 14 competitors',
      detail: '1 eligible price comparison: Andertons.',
      tone: 'success',
    })
    expect(keyFinding({ ...run, status: 'FAILED', error: 'Search provider down' }, result)).toMatchObject({
      headline: 'Run failed',
      detail: 'Search provider down',
      tone: 'danger',
    })
    expect(keyFinding({ ...run, status: 'RUNNING' }, result)).toMatchObject({ headline: 'Run in progress', tone: 'info' })

    for (const c of result.products[0].matching!.candidates) c.comparison = { ...c.comparison, eligible: false }
    result.intelligence_mode = 'LLM_DISABLED'
    expect(keyFinding(run, result)).toMatchObject({
      detail: 'No price comparison: LLM 2 extraction is off, so competitor prices were not extracted.',
      tone: 'warning',
    })
    expect(keyFinding({ ...run, status: 'CANCELLED' }, result).detail).toMatch(/^Cancelled before finishing/)
    const none = { ...run, summary: { ...run.summary, exact_matches: 0 } }
    expect(keyFinding(none, result)).toMatchObject({ headline: 'No exact match across 14 competitors', tone: 'neutral' })
  })

  it('explains price comparability from backend statuses and reasons only', () => {
    const { run, result } = fixture()
    const gate = priceGate(run, result)
    expect(gate).toMatchObject({ exactMatches: 3, llmDisabledReason: null, comparable: 1, validPrices: 1 })
    expect(gate.pricesExtracted).toBe(result.products[0].matching!.candidates.filter((c) => c.price.status !== 'NOT_EXTRACTED').length)
    // reasons come verbatim from comparison.reasons of exact matches that were not compared
    // (auto-approval never grants benchmark permission, D-15: Beckett's is searched but not a benchmark)
    expect(gate.blockers.map((b) => b.reason)).toEqual([
      'Price status is NOT_EXTRACTED',
      "beckettsmusic.co.uk is not marked as a price benchmark (specialist_retailer)",
      'Price status is UNSUPPORTED',
    ])
    result.intelligence_mode = 'LLM_DISABLED'
    result.llm2_disabled_reason = 'LLM2_MODEL not set'
    expect(priceGate(run, result).llmDisabledReason).toBe('LLM2_MODEL not set')
  })
})
