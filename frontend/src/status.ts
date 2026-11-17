// One table for every status value the backend returns: tone (colour), icon and label.
// Status is never shown by colour alone: StatusPill always renders the icon and label.
import { label as fallbackLabel } from './format'
import type { Connection } from './runStream'

export type Tone = 'success' | 'info' | 'similar' | 'warning' | 'danger' | 'neutral'

export const TONE_ICON: Record<Tone, string> = {
  success: '✓',
  info: '●',
  similar: '≈',
  warning: '!',
  danger: '✕',
  neutral: '–',
}

// Values that mean the same thing in every context.
const TONES: Record<string, Tone> = {
  // run / stage / product
  COMPLETED: 'success',
  RUNNING: 'info',
  PARTIAL: 'warning',
  PARTIALLY_COMPLETED: 'warning',
  FAILED: 'danger',
  SKIPPED: 'neutral',
  CANCELLED: 'neutral',
  PENDING: 'neutral',
  // matching
  EXACT_MATCH: 'success',
  SIMILAR_MATCH: 'similar',
  REVIEW_REQUIRED: 'warning',
  NO_MATCH: 'neutral',
  HIGH: 'success',
  MEDIUM: 'info',
  LOW: 'warning',
  MATCH: 'success',
  UNVERIFIED: 'warning',
  NOT_APPLICABLE: 'neutral',
  NEW: 'success',
  USED: 'warning',
  REFURBISHED: 'warning',
  UNKNOWN: 'neutral',
  // prices and evidence
  VALID: 'success',
  WRONG_CURRENCY: 'warning',
  MISSING: 'warning',
  NOT_EXTRACTED: 'neutral',
  EXCLUDED_SALE_PRICE: 'warning',
  AMBIGUOUS: 'warning',
  UNSUPPORTED: 'warning',
  CONFLICT: 'danger',
  INVALID: 'danger',
  VERIFIED: 'success',
  STALE: 'warning',
  NOT_FOUND: 'neutral',
  // domains
  PRECONFIGURED: 'success',
  AUTO_APPROVED: 'info',
  APPROVAL_REQUIRED: 'warning',
  APPROVED: 'success',
  REJECTED: 'danger',
  // retrieval / extraction
  OK: 'success',
  EMPTY: 'warning',
  BLOCKED: 'warning',
  SKIPPED_NO_LLM: 'neutral',
  SKIPPED_NOT_RETRIEVED: 'neutral',
  SKIPPED_LLM_DISABLED: 'neutral',
}

// A review queue entry that is PENDING needs a decision: warning, not neutral.
const REVIEW_TONES: Record<string, Tone> = { PENDING: 'warning' }

const LABELS: Record<string, string> = {
  PARTIALLY_COMPLETED: 'Partially completed',
  EXACT_MATCH: 'Exact match',
  SIMILAR_MATCH: 'Similar',
  REVIEW_REQUIRED: 'Review required',
  NO_MATCH: 'No match',
  HIGH: 'High confidence',
  MEDIUM: 'Medium confidence',
  LOW: 'Low confidence',
  PRECONFIGURED: 'Configured',
  AUTO_APPROVED: 'Auto-approved (this run)',
  APPROVAL_REQUIRED: 'Approval required',
  EXCLUDED_SALE_PRICE: 'Sale price excluded',
  WRONG_CURRENCY: 'Wrong currency',
  NOT_EXTRACTED: 'Not extracted',
  NOT_APPLICABLE: 'Not applicable',
  NOT_FOUND: 'Not found',
  SKIPPED_NO_LLM: 'Skipped (no LLM 2)',
  SKIPPED_NOT_RETRIEVED: 'Skipped (not retrieved)',
  SKIPPED_LLM_DISABLED: 'Skipped (no LLM 1)',
  OK: 'Retrieved',
}

export type StatusKind = 'default' | 'review'

export function statusMeta(value: string | null | undefined, kind: StatusKind = 'default') {
  const v = value ?? 'UNKNOWN'
  const tone: Tone = (kind === 'review' ? REVIEW_TONES[v] : undefined) ?? TONES[v] ?? 'neutral'
  const text = kind === 'review' && v === 'PENDING' ? 'Pending review' : (LABELS[v] ?? fallbackLabel(v))
  return { tone, icon: TONE_ICON[tone], label: text }
}

export const KNOWN_STATUS_VALUES = Object.keys(TONES)

export const CONNECTION_TEXT: Record<Connection, string> = {
  idle: 'No run selected',
  connecting: 'Connecting…',
  live: 'Live',
  reconnecting: 'Reconnecting…',
  polling: 'Polling for updates',
  closed: 'Finished',
  expired: 'Run expired',
  error: 'Connection error',
}
