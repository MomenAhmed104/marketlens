import { describe, expect, it } from 'vitest'
import { KNOWN_STATUS_VALUES, statusMeta, TONE_ICON } from './status'

// Every status value the backend can send (api.ts enums).
const BACKEND_VALUES = [
  'PENDING', 'RUNNING', 'COMPLETED', 'PARTIALLY_COMPLETED', 'FAILED', 'CANCELLED', 'PARTIAL', 'SKIPPED',
  'EXACT_MATCH', 'SIMILAR_MATCH', 'REVIEW_REQUIRED', 'NO_MATCH', 'HIGH', 'MEDIUM', 'LOW',
  'MATCH', 'CONFLICT', 'UNVERIFIED', 'NOT_APPLICABLE', 'NEW', 'USED', 'REFURBISHED', 'UNKNOWN',
  'VALID', 'WRONG_CURRENCY', 'MISSING', 'NOT_EXTRACTED', 'EXCLUDED_SALE_PRICE', 'AMBIGUOUS', 'UNSUPPORTED', 'INVALID',
  'VERIFIED', 'STALE', 'NOT_FOUND',
  'PRECONFIGURED', 'AUTO_APPROVED', 'APPROVAL_REQUIRED', 'APPROVED', 'REJECTED',
  'OK', 'EMPTY', 'BLOCKED', 'SKIPPED_NO_LLM', 'SKIPPED_NOT_RETRIEVED', 'SKIPPED_LLM_DISABLED',
  // Product Intelligence Agent: final status, LLM 2 proposals, agreement, availability, price kinds
  'EQUIVALENT_VARIANT', 'SIMILAR_ALTERNATIVE', 'NOT_A_MATCH', 'SKIPPED_NOT_ELIGIBLE',
  'AGREES', 'PYTHON_STRICTER', 'LLM_STRICTER', 'NOT_AVAILABLE',
  'IN_STOCK', 'OUT_OF_STOCK', 'PREORDER', 'BACKORDER', 'DISCONTINUED', 'DIRECT', 'ALTERNATIVE',
]

describe('statusMeta', () => {
  it('maps every backend status value explicitly to a tone, icon and label', () => {
    for (const v of BACKEND_VALUES) {
      expect(KNOWN_STATUS_VALUES).toContain(v)
      const m = statusMeta(v)
      expect(m.icon).toBe(TONE_ICON[m.tone])
      expect(m.label.length).toBeGreaterThan(0)
    }
  })

  it('keeps match statuses visually distinct', () => {
    const tones = ['EXACT_MATCH', 'EQUIVALENT_VARIANT', 'SIMILAR_MATCH', 'REVIEW_REQUIRED', 'NO_MATCH'].map(
      (v) => statusMeta(v).tone,
    )
    expect(new Set(tones).size).toBe(5)
    expect(statusMeta('SIMILAR_MATCH').tone).not.toBe(statusMeta('RUNNING').tone)
  })

  it('treats a pending review as needing action, unlike a pending stage', () => {
    expect(statusMeta('PENDING').tone).toBe('neutral')
    expect(statusMeta('PENDING', 'review')).toMatchObject({ tone: 'warning', label: 'Pending review' })
  })

  it('falls back to neutral for unknown values', () => {
    expect(statusMeta('SOMETHING_NEW')).toMatchObject({ tone: 'neutral', label: 'Something new' })
    expect(statusMeta(null).tone).toBe('neutral')
  })
})
