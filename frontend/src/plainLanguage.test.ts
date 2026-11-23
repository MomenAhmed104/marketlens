import { describe, expect, it } from 'vitest'
import { EVENT_LABEL, eventLabel, explain, humanizeCodes, PRICE_STATUS_TEXT, priceStatusText } from './plainLanguage'
import { fixture } from './test/fakes'

const RAW_CODE = /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/

describe('plain-language translation', () => {
  it('explains every backend price status in a sentence', () => {
    for (const s of ['VALID', 'WRONG_CURRENCY', 'MISSING', 'NOT_EXTRACTED', 'EXCLUDED_SALE_PRICE', 'AMBIGUOUS',
                     'UNSUPPORTED', 'CONFLICT', 'INVALID']) {
      expect(PRICE_STATUS_TEXT[s]).toMatch(/\.$/)
    }
    expect(explain('Price status is EXCLUDED_SALE_PRICE')).toBe(
      'Excluded: this appears to be a previous or reference price, not the current selling price.')
    expect(explain('Price status is MISSING')).toBe('No price found on the product page.')
    expect(explain('Price status is CONFLICT')).toBe('Conflicting price information; excluded from comparison.')
  })

  it('rewrites the other comparison and price reasons the backend produces', () => {
    expect(explain('Match status is SIMILAR_MATCH, not EXACT_MATCH')).toBe(
      'Only exact matches are price-compared; this result is similar.')
    expect(explain('Match status is NO_MATCH: price not shown')).toBe(
      'No alternative-product price is shown for a no match result.')
    expect(explain('Phase 5 price status UNSUPPORTED: amount does not appear in its quote')).toBe(
      'The price could not be confirmed in the page text; excluded from comparison. (amount does not appear in its quote)')
    expect(explain('Price evidence is STALE')).toBe('The page was retrieved too long ago for its price to count as current.')
    expect(explain('Condition USED is not comparable to new')).toBe('Used: not compared with a new product.')
    expect(explain('gibson.com is not marked as a price benchmark (manufacturer)')).toBe(
      'gibson.com is not a price benchmark (manufacturer).')
    expect(explain('beckettsmusic.co.uk is not marked as a price benchmark (specialist_retailer)')).toBe(
      'beckettsmusic.co.uk is not a price benchmark (specialist retailer).')
  })

  it('never shows a raw code for any reason in the dashboard fixture', () => {
    const f = fixture()
    const texts = new Set<string>()
    for (const p of f.result.products) {
      for (const c of p.matching?.candidates ?? []) {
        ;[...c.comparison.reasons, ...c.price.reasons, ...c.match_reasons, ...c.review_reasons].forEach((r) => texts.add(r))
      }
    }
    for (const c of f.result.discovery?.candidates ?? []) (c.decision_reasons ?? []).forEach((r) => texts.add(r))
    for (const e of f.events) texts.add(e.message)
    expect(texts.size).toBeGreaterThan(10)
    for (const t of texts) expect(explain(t), t).not.toMatch(RAW_CODE)
  })

  it('falls back safely for unknown codes and odd input', () => {
    expect(explain('Price status is SOMETHING_NEW')).toBe('This price could not be used for comparison.')
    expect(priceStatusText(undefined)).toBe('This price could not be used for comparison.')
    expect(explain('Reviewer decision APPROVED (overrides automatic APPROVAL_REQUIRED)')).toBe(
      'Reviewer decision APPROVED (overrides automatic approval required)')
    expect(explain('Brand new FLUX_CAPACITOR_MODE engaged')).toBe('Brand new flux capacitor mode engaged')
    expect(explain('')).toBe('')
    expect(explain(null)).toBe('')
    expect(humanizeCodes('See https://example.com/a_b_c for type other_thing')).toBe(
      'See https://example.com/a_b_c for type other_thing') // URLs are never rewritten
  })

  it('labels every event type plainly, with a readable fallback', () => {
    for (const [type, text] of Object.entries(EVENT_LABEL)) {
      expect(eventLabel(type)).toBe(text)
      expect(text).not.toMatch(/_/)
    }
    expect(eventLabel('brand_new_event')).toBe('Brand new event')
    const types = new Set(fixture().events.map((e) => e.type))
    for (const t of types) expect(EVENT_LABEL[t], t).toBeDefined()
  })

  it('explains the Stage 8.2 not-searched reasons without raw setting names', () => {
    const off = explain('Automatic search is switched off (PIPELINE_SEARCH_AUTO_APPROVED=false)')
    expect(off).toMatch(/switched off in the server settings/)
    expect(off).not.toMatch(RAW_CODE)
    const cap = explain('Automatically searchable, but over the per-run cap of 5 automatic domain(s) (MAX_AUTO_SEARCH_TARGETS); ranked 7 of 9')
    expect(cap).toBe("Over this run's limit of 5 automatic domain(s): ranked 7 of 9, so not searched.")
  })
})
