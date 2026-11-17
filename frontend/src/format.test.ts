import { describe, expect, it } from 'vitest'
import { diffDirection, label, money, plural, signedGbp, when } from './format'

describe('format', () => {
  it('distinguishes a missing price from zero', () => {
    expect(money(null, 'GBP')).toBe('missing')
    expect(money('0.00', 'GBP')).toBe('£0.00')
    expect(money('189.99', 'GBP')).toBe('£189.99')
    expect(money('10.00', 'EUR')).toBe('€10.00')
    expect(money('10.00', null)).toBe('10.00') // no currency: shown as given, never assumed
  })

  it('formats the backend difference with its sign and direction', () => {
    expect(signedGbp('-0.99')).toBe('−£0.99')
    expect(signedGbp('12.50')).toBe('+£12.50')
    expect(signedGbp('0.00')).toBe('£0.00')
    expect(diffDirection('-0.99')).toBe('competitor lower')
    expect(diffDirection('3')).toBe('competitor higher')
    expect(diffDirection('0.00')).toBe('same price')
  })

  it('labels enum values', () => {
    expect(label('SIMILAR_MATCH')).toBe('Similar match')
    expect(label(null)).toBe('unknown')
  })

  it('formats dates without seconds and pluralises counts', () => {
    expect(when('2026-11-13T17:02:39Z')).toMatch(/^13 Nov 2026, \d{2}:\d{2}$/)
    expect(when(null)).toBe('unknown')
    expect(plural(1, 'candidate')).toBe('1 candidate')
    expect(plural(3, 'candidate')).toBe('3 candidates')
    expect(plural(0, 'match', 'matches')).toBe('0 matches')
  })
})
