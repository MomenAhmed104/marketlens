// Overview data, derived from one run's backend results.
// Only counting and grouping of backend values happens here: no price arithmetic,
// no percentages. Every number traces to a backend field (see docs/dashboard.md).
import type {
  CandidateMatch,
  DomainApprovalStatus,
  ExecutionRun,
  MatchStatus,
  PipelineResult,
  ProductRunResult,
  SearchTarget,
} from './api'

export const MATCH_STATUSES: MatchStatus[] = ['EXACT_MATCH', 'EQUIVALENT_VARIANT', 'REVIEW_REQUIRED', 'SIMILAR_MATCH', 'NO_MATCH']

export interface OverviewKpis {
  productsCompleted: number
  productsTotal: number
  productsFailed: number
  competitorsSearched: number
  searchedByApproval: Partial<Record<DomainApprovalStatus, number>>
  exactMatches: number
  comparablePrices: number
  productsNeedingReview: number
  reviewCandidates: number
}

export function overviewKpis(run: ExecutionRun, result: PipelineResult | null): OverviewKpis {
  const s = run.summary
  const targets = result?.search_targets ?? []
  const searchedByApproval: Partial<Record<DomainApprovalStatus, number>> = {}
  for (const t of targets) searchedByApproval[t.approval_status] = (searchedByApproval[t.approval_status] ?? 0) + 1
  return {
    productsCompleted: s.products_completed,
    productsTotal: s.products_total,
    productsFailed: s.products_failed,
    competitorsSearched: targets.length,
    searchedByApproval,
    exactMatches: s.exact_matches,
    comparablePrices: s.comparable_prices,
    productsNeedingReview: (result?.products ?? []).filter((p) => (p.matching?.counts.REVIEW_REQUIRED ?? 0) > 0).length,
    reviewCandidates: s.review_required,
  }
}

export interface CoverageRow {
  target: SearchTarget
  counts: Record<MatchStatus, number>
  candidates: number
  searchErrors: string[] // "SB-001: <error>"
}

/** One row per searched domain: candidates per match status across the run's products. */
export function coverageRows(result: PipelineResult | null): CoverageRow[] {
  if (!result) return []
  const rows = new Map<string, CoverageRow>(
    result.search_targets.map((t) => [
      t.domain,
      { target: t, counts: { EXACT_MATCH: 0, EQUIVALENT_VARIANT: 0, REVIEW_REQUIRED: 0, SIMILAR_MATCH: 0, NO_MATCH: 0 }, candidates: 0, searchErrors: [] },
    ]),
  )
  for (const p of result.products) {
    for (const c of p.matching?.candidates ?? []) {
      const row = rows.get(c.competitor.domain)
      if (!row) continue
      row.counts[c.match_status]++
      row.candidates++
    }
    for (const d of p.intelligence?.domains ?? []) {
      const row = rows.get(d.domain)
      if (row) row.searchErrors.push(...d.errors.map((e) => `${p.product_id}: ${e}`))
    }
  }
  return [...rows.values()]
}

export interface EligibleComparison {
  productId: string
  productName: string
  candidate: CandidateMatch
}

/** Candidates the backend marked comparable (comparison.eligible). Nothing else. */
export function eligibleComparisons(result: PipelineResult | null): EligibleComparison[] {
  return (result?.products ?? []).flatMap((p) =>
    (p.matching?.candidates ?? [])
      .filter((c) => c.comparison.eligible && c.comparison.difference_gbp !== null)
      .map((c) => ({ productId: p.product_id, productName: p.matching!.product_name, candidate: c })),
  )
}

export interface ProductInsights {
  noCandidates: ProductRunResult[] // nothing found on the searched domains (not proof of unavailability)
  noExactMatch: ProductRunResult[] // candidates exist, none exact
  failed: ProductRunResult[]
}

export function productInsights(result: PipelineResult | null): ProductInsights {
  const products = result?.products ?? []
  const done = products.filter((p) => p.status === 'COMPLETED' && p.matching && p.matching.counts.EXACT_MATCH === 0)
  return {
    noCandidates: done.filter((p) => p.matching!.candidates.length === 0),
    noExactMatch: done.filter((p) => p.matching!.candidates.length > 0),
    failed: products.filter((p) => p.status === 'FAILED'),
  }
}

// ---------------------------------------------------------------- hero and price explainer

export type FindingTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral'

export interface KeyFinding {
  headline: string
  detail: string
  tone: FindingTone
}

const n = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`

/**
 * The hero's one-sentence finding, chosen by fixed rules (no invented metrics):
 * failed run → in progress → exact matches and the price outcome.
 */
export function keyFinding(run: ExecutionRun, result: PipelineResult | null): KeyFinding {
  const s = run.summary
  const competitors = result?.search_targets.length ?? 0
  if (run.status === 'FAILED') {
    return { headline: 'Run failed', detail: run.error ?? 'No product completed.', tone: 'danger' }
  }
  if (run.status === 'PENDING' || run.status === 'RUNNING') {
    return {
      headline: 'Run in progress',
      detail: `${s.products_completed} of ${n(s.products_total, 'product')} completed so far; results appear as each product finishes.`,
      tone: 'info',
    }
  }
  const headline =
    s.exact_matches > 0
      ? `${n(s.exact_matches, 'exact match', 'exact matches')} across ${n(competitors, 'competitor')}`
      : `No exact match across ${n(competitors, 'competitor')}`
  const prefix = run.status === 'CANCELLED' ? 'Cancelled before finishing; results so far. ' : ''
  const comparisons = eligibleComparisons(result)
  let detail: string
  if (comparisons.length === 1) {
    const c = comparisons[0].candidate
    detail = `1 eligible price comparison: ${c.competitor.company_name ?? c.competitor.domain}.`
  } else if (comparisons.length > 1) {
    detail = `${comparisons.length} eligible price comparisons.`
  } else if (result?.intelligence_mode === 'LLM_DISABLED') {
    detail = 'No price comparison: LLM 2 extraction is off, so competitor prices were not extracted.'
  } else {
    detail = 'No eligible price comparison in this run.'
  }
  return {
    headline,
    detail: prefix + detail,
    tone: comparisons.length > 0 ? 'success' : s.exact_matches > 0 ? 'warning' : 'neutral',
  }
}

export interface PriceGate {
  exactMatches: number
  llmDisabledReason: string | null // set when LLM 2 is off for this run
  pricesExtracted: number // candidates whose price was looked for (status other than NOT_EXTRACTED)
  validPrices: number // price.status VALID
  comparable: number
  blockers: { reason: string; count: number }[] // backend comparison.reasons on exact matches not compared
}

/** Why a run has (or lacks) price comparisons, from backend statuses and reasons only. */
export function priceGate(run: ExecutionRun, result: PipelineResult | null): PriceGate {
  const candidates = (result?.products ?? []).flatMap((p) => p.matching?.candidates ?? [])
  const blockers = new Map<string, number>()
  for (const c of candidates) {
    if (c.match_status !== 'EXACT_MATCH' || c.comparison.eligible) continue
    for (const r of c.comparison.reasons) blockers.set(r, (blockers.get(r) ?? 0) + 1)
  }
  return {
    exactMatches: run.summary.exact_matches,
    llmDisabledReason:
      result?.intelligence_mode === 'LLM_DISABLED' ? (result.llm2_disabled_reason ?? 'LLM 2 is not configured') : null,
    pricesExtracted: candidates.filter((c) => c.price.status !== 'NOT_EXTRACTED').length,
    validPrices: candidates.filter((c) => c.price.status === 'VALID').length,
    comparable: run.summary.comparable_prices,
    blockers: [...blockers].map(([reason, count]) => ({ reason, count })).sort((a, b) => b.count - a.count),
  }
}
