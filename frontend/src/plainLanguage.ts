// Plain-English text for backend codes and reasons (Stage 8.2).
//
// The backend keeps its technical values (statuses such as EXCLUDED_SALE_PRICE, and reasons
// such as "Price status is MISSING"); the API and stored data are unchanged. This module only
// rewrites what people read. Known reason patterns get a full sentence; any other code-like
// token is turned into readable words, so an unknown code never shows raw and never throws.
import { label } from './format'
import { statusMeta } from './status'

/** What each price status means for a reader. */
export const PRICE_STATUS_TEXT: Record<string, string> = {
  VALID: 'Verified current selling price.',
  WRONG_CURRENCY: 'The price is not in GBP, so it is not compared.',
  MISSING: 'No price found on the product page.',
  NOT_EXTRACTED: 'The price was not read from this page.',
  EXCLUDED_SALE_PRICE: 'Excluded: this appears to be a previous or reference price, not the current selling price.',
  AMBIGUOUS: 'More than one possible price, or a conditional price, was shown; excluded from comparison.',
  UNSUPPORTED: 'The price could not be confirmed in the page text; excluded from comparison.',
  CONFLICT: 'Conflicting price information; excluded from comparison.',
  INVALID: 'The price record is incomplete or inconsistent; excluded from comparison.',
}
const PRICE_FALLBACK = 'This price could not be used for comparison.'

const EVIDENCE_TEXT: Record<string, string> = {
  VERIFIED: 'The price evidence is verified.',
  UNVERIFIED: 'The price evidence could not be verified.',
  STALE: 'The page was retrieved too long ago for its price to count as current.',
  NOT_FOUND: 'No price evidence was found.',
  CONFLICT: 'The price evidence is contradictory.',
}

const CONDITION_TEXT: Record<string, string> = {
  USED: 'Used',
  REFURBISHED: 'Refurbished or B-stock',
  NEW: 'New',
  UNKNOWN: 'Condition not stated',
}

/** Readable label for event types shown in the run feed. */
export const EVENT_LABEL: Record<string, string> = {
  run_started: 'Run started',
  run_completed: 'Run completed',
  run_failed: 'Run failed',
  run_cancelled: 'Run cancelled',
  stage_started: 'Stage started',
  stage_completed: 'Stage finished',
  product_loaded: 'Product loaded',
  product_completed: 'Product finished',
  product_failed: 'Product failed',
  competitor_discovered: 'Competitor discovered',
  domain_auto_approved: 'Automatically eligible',
  domain_approval_required: 'Needs review',
  known_domains_loaded: 'Search targets chosen',
  search_started: 'Search started',
  page_retrieved: 'Pages retrieved',
  extraction_skipped: 'Extraction skipped',
  competitor_product_extracted: 'Product details extracted',
  match_validated: 'Match checked',
  price_validated: 'Price checked',
}

export const eventLabel = (type: string): string => EVENT_LABEL[type] ?? (label(type) || 'Event')

export const priceStatusText = (status: string | null | undefined): string =>
  (status && PRICE_STATUS_TEXT[status]) || PRICE_FALLBACK

const lower = (s: string) => s.charAt(0).toLowerCase() + s.slice(1)
const codeLabel = (code: string) => statusMeta(code).label

// Upper-case codes with at least one underscore (EXCLUDED_SALE_PRICE, SKIPPED_LLM_DISABLED).
const UPPER_CODE = /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/g
// Lower-case snake identifiers (specialist_retailer). Skipped in text that contains a URL.
const LOWER_CODE = /\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g

/** Rewrites any remaining code-like tokens into words. Never throws. */
export function humanizeCodes(text: string): string {
  let out = text.replace(UPPER_CODE, (code) => lower(codeLabel(code)))
  if (!/https?:\/\//.test(out)) out = out.replace(LOWER_CODE, (code) => code.replace(/_/g, ' '))
  return out
}

const PATTERNS: [RegExp, (...g: string[]) => string][] = [
  [/^Price status is ([A-Z_]+)\.?$/, (s) => priceStatusText(s)],
  [/^Price evidence is ([A-Z_]+)\.?$/, (s) => EVIDENCE_TEXT[s] ?? 'The price evidence could not be verified.'],
  [/^Match status is ([A-Z_]+), not EXACT_MATCH\.?$/,
    (s) => `Only exact matches are price-compared; this result is ${lower(codeLabel(s))}.`],
  [/^Match status is ([A-Z_]+): price not shown\.?$/,
    (s) => `No alternative-product price is shown for a ${lower(codeLabel(s))} result.`],
  [/^Phase 5 price status ([A-Z_]+)(?::\s*(.*))?$/s,
    (s, note) => (note ? `${priceStatusText(s)} (${humanizeCodes(note)})` : priceStatusText(s))],
  [/^Condition ([A-Z_]+) is not comparable to new\.?$/,
    (s) => `${CONDITION_TEXT[s] ?? 'This condition'}: not compared with a new product.`],
  [/^Same model but condition is ([A-Z_]+): not equivalent to new\.?$/,
    (s) => `Same model, but ${lower(CONDITION_TEXT[s] ?? 'a different condition')}: not equivalent to new.`],
  [/^(\S+) is not marked as a price benchmark \((.+)\)\.?$/,
    (d, t) => `${d} is not a price benchmark (${humanizeCodes(t)}).`],
  [/^Automatic search is switched off \(PIPELINE_SEARCH_AUTO_APPROVED=false\)\.?$/,
    () => 'Automatic search is switched off in the server settings, so it was not searched. Approve it to search it.'],
  [/^Automatically searchable, but over the per-run cap of (\d+) automatic domain\(s\) \(MAX_AUTO_SEARCH_TARGETS\); ranked (\d+) of (\d+)\.?$/,
    (cap, r, n) => `Over this run's limit of ${cap} automatic domain(s): ranked ${r} of ${n}, so not searched.`],
]

/** A backend reason or message, in plain English. Unknown text passes through with codes humanized. */
export function explain(text: string | null | undefined): string {
  if (!text) return ''
  const t = text.trim()
  for (const [re, fn] of PATTERNS) {
    const m = t.match(re)
    if (m) return fn(...m.slice(1))
  }
  return humanizeCodes(t)
}
