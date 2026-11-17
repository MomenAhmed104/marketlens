// Typed client for the backend API. Types mirror backend Pydantic schemas.

export interface Health {
  status: string
  products: number
  competitors: number
}

// --- Competitor review (Phase 4) ---------------------------------------------

export type CompanyType =
  | 'retailer'
  | 'specialist_retailer'
  | 'manufacturer'
  | 'marketplace'
  | 'directory'
  | 'other'

export const COMPANY_TYPES: CompanyType[] = [
  'retailer',
  'specialist_retailer',
  'manufacturer',
  'marketplace',
  'directory',
  'other',
]

// Mirrors BENCHMARK_ELIGIBLE_TYPES on the backend (which enforces it).
export const BENCHMARK_ELIGIBLE_TYPES: CompanyType[] = ['retailer', 'specialist_retailer']

export type ReviewStatus = 'PENDING' | 'APPROVED' | 'REJECTED'
export type ReviewDecision = 'APPROVED' | 'REJECTED'

export interface DomainDecision {
  domain: string
  decision: ReviewDecision
  company_name: string | null
  company_type: CompanyType | null
  benchmark_eligible: boolean
  reviewer: string
  decided_at: string
  notes: string | null
  evidence_urls: string[]
  source_run_id: string | null
  version: number
}

/** Backend competitor classification (LLM 1 proposal + deterministic code safeguards). */
export type BackendCompetitorClass = 'DIRECT_COMPETITOR' | 'RELATED_RETAILER' | 'NEEDS_REVIEW'

export interface CandidateSummary {
  domain: string
  homepage_url: string
  review_status: ReviewStatus
  decision: DomainDecision | null
  search_eligible: boolean
  benchmark_eligible: boolean
  discovery_mode: 'LLM_ENABLED' | 'LLM_DISABLED'
  classification_status: 'COMPLETED' | 'SKIPPED_LLM_DISABLED' | 'FAILED' | 'MISSING'
  llm_classified: boolean
  automatic_status: string
  proposed_company_name: string | null
  name_verified: boolean
  proposed_company_type: CompanyType | null
  confidence: number | null
  relevance: number | null
  uk_seller: 'yes' | 'no' | 'unknown'
  uk_signals: string[]
  products_matched: string[]
  known_marketplace: boolean
  // Final class (the source of truth) and LLM 1's raw proposal. Null on records stored
  // before classification existed (not yet backfilled); optional for older fixtures.
  competitor_class?: BackendCompetitorClass | null
  proposed_competitor_class?: BackendCompetitorClass | null
  evidence_count: number
  first_seen_at: string
  last_seen_at: string
  times_seen: number
  last_run_id: string
}

export interface EvidenceItem {
  url: string
  canonical_url: string
  hostname: string
  title: string | null
  snippet: string | null
  kind: 'discovery_result' | 'domain_profile'
  query_text: string
  product_id: string | null
  provider: string
  provider_request_id: string | null
  retrieved_at: string
  mentions_product_model: boolean
  mentions_brand: boolean
}

export interface CandidateDetail extends CandidateSummary {
  evidence: EvidenceItem[]
  cited_evidence_urls: string[]
  relevant_categories: string[]
  rationale: string | null
  automatic_reasons: string[]
  validation_notes: string[]
  history: DomainDecision[]
}

export interface DecisionInput {
  reviewer: string
  company_type: CompanyType | null
  company_name: string | null
  benchmark_eligible: boolean
  notes: string | null
}

export interface DiscoveryRunSummary {
  run_id: string
  mode: 'LLM_ENABLED' | 'LLM_DISABLED'
  llm_disabled_reason: string | null
  candidates: unknown[]
  warnings: string[]
}

// --- Catalogue (Phase 1) -------------------------------------------------------
// Decimal fields arrive as JSON strings (Pydantic). The UI formats them and
// never does arithmetic on them: calculations stay in Python.

export interface CatalogueProvenance {
  verification_status: string
  source_type: string
  evidence_url: string
  last_checked: string
  price_note: string | null
  price_basis: string | null
  price_snapshot_date: string
  price_audit_status: string | null
  price_audit_note: string | null
  url_research_status: string | null
}

export interface StompboxProduct {
  product_id: string
  product_name: string
  brand: string
  manufacturer_model: string
  category: string
  subcategory: string
  stompbox_sku: string | null
  displayed_price: string | null // null = missing, never 0
  currency: string
  condition: string | null
  availability: string | null
  specifications: string | null
  product_url: string
  search_keywords: string | null
  provenance: CatalogueProvenance
}

export interface ProductListResponse {
  total: number
  items: StompboxProduct[]
}

// Configured competitor from the workbook (GET /api/competitors).
export interface CompetitorDomain {
  competitor_id: string | null
  name: string
  domain: string
  homepage_url: string
  company_type: CompanyType
  classification: string | null
  priority: string | null
  price_benchmark: boolean
  approval_status: DomainApprovalStatus
  source: string
  notes: string | null
  relevant_categories: string[]
}

// --- Shared enums --------------------------------------------------------------

export type DomainApprovalStatus = 'PRECONFIGURED' | 'AUTO_APPROVED' | 'APPROVAL_REQUIRED' | 'APPROVED' | 'REJECTED'
export type MatchStatus = 'EXACT_MATCH' | 'SIMILAR_MATCH' | 'REVIEW_REQUIRED' | 'NO_MATCH'
export type EvidenceStatus = 'VERIFIED' | 'UNVERIFIED' | 'STALE' | 'NOT_FOUND' | 'CONFLICT'
export type LlmMode = 'LLM_ENABLED' | 'LLM_DISABLED'

// --- Configuration (Phases 3 and 5) --------------------------------------------

export interface SearchTarget {
  domain: string
  company_name: string | null
  company_type: CompanyType | null
  approval_status: DomainApprovalStatus
  price_benchmark: boolean
}

export interface SkippedTarget {
  domain: string
  company_type: string | null
  reason: string
}

export interface DiscoveryConfig {
  mode: LlmMode
  llm_disabled_reason: string | null
  llm1_model: string | null
  search_provider: string | null
  thresholds?: { auto_approve_min_relevance: number } // reported by the backend; optional in older fixtures
}

export interface IntelligenceConfig {
  mode: LlmMode
  llm_disabled_reason: string | null
  llm2_model: string | null
  search_provider: string | null
  search_targets: SearchTarget[]
  skipped_targets: SkippedTarget[]
  max_products_per_run: number
}

// --- Discovery result (Phase 3), fields used by the dashboard -------------------

export interface DiscoveredCompetitor {
  domain: string
  homepage_url: string
  classification_status: CandidateSummary['classification_status']
  company_name: string | null
  company_type: CompanyType | null
  competitor_class?: BackendCompetitorClass | null
  proposed_competitor_class?: BackendCompetitorClass | null
  approval_status: DomainApprovalStatus
  automatic_approval_status: DomainApprovalStatus | null
  price_benchmark: boolean
  review_decision: DomainDecision | null
}

export interface DiscoveryUsage {
  search_requests: number
  search_errors: number
  search_cost_usd: string
  llm_model: string | null
  llm_calls: number
  llm_cost_usd: string | null
}

export interface DiscoveryRunResult {
  run_id: string
  mode: LlmMode
  llm_disabled_reason: string | null
  candidates: DiscoveredCompetitor[]
  excluded: { domain: string; reason: string; evidence_count: number }[]
  usage: DiscoveryUsage
  warnings: string[]
}

// --- Product intelligence (Phase 5) --------------------------------------------

export type RetrievalStatus = 'OK' | 'EMPTY' | 'BLOCKED' | 'FAILED'
export type ExtractionStatus = 'COMPLETED' | 'SKIPPED_NO_LLM' | 'SKIPPED_NOT_RETRIEVED' | 'FAILED'

export interface SearchHit {
  domain: string
  query_text: string
  provider: string
  rank: number
  url: string
  canonical_url: string
  title: string | null
  retrieved_at: string
}

export interface PageRecord {
  status: RetrievalStatus | null
  provider: string
  requested_url: string
  final_url: string | null
  title: string | null
  retrieved_at: string | null
  cache_source: string | null
  error: string | null
}

export interface CompetitorProduct {
  candidate_id: string
  product_url: string
  search: SearchHit
  page: PageRecord
  extraction_status: ExtractionStatus
  extraction_error: string | null
  product_title: string | null
  availability: string | null
}

export interface DomainSearchSummary {
  domain: string
  queries: string[]
  results: number
  pages_selected: number
  errors: string[]
}

export interface ProductIntelligence {
  queries: string[]
  domains: DomainSearchSummary[]
  candidates: CompetitorProduct[]
}

export interface IntelligenceUsage {
  search_requests: number
  search_errors: number
  search_cost_usd: string
  retrieval_requests: number
  pages_requested: number
  pages_ok: number
  retrieval_cost_usd: string
  llm_model: string | null
  llm_calls: number
  llm_failures: number
  llm_cost_usd: string | null
}

// --- Matching and price validation (Phase 6) -----------------------------------

export type CheckResult = 'MATCH' | 'CONFLICT' | 'UNVERIFIED' | 'NOT_APPLICABLE'
export type PriceStatus =
  | 'VALID'
  | 'WRONG_CURRENCY'
  | 'MISSING'
  | 'NOT_EXTRACTED'
  | 'EXCLUDED_SALE_PRICE'
  | 'AMBIGUOUS'
  | 'UNSUPPORTED'
  | 'CONFLICT'
  | 'INVALID'

export interface EvidenceRef {
  source: string
  text: string
}

export interface AttributeCheck {
  attribute: string
  result: CheckResult
  critical: boolean
  expected: string | null
  found: string[]
  evidence: EvidenceRef[]
  note: string | null
}

export interface PriceValidation {
  status: PriceStatus
  evidence_status: EvidenceStatus
  price: string | null
  currency: string | null
  price_text: string | null
  quote: string | null
  source_url: string
  retrieved_at: string | null
  sale_indicated: boolean
  reasons: string[]
}

export interface PriceComparison {
  eligible: boolean
  reasons: string[]
  stompbox_price: string | null
  stompbox_currency: string | null
  competitor_price: string | null
  difference_gbp: string | null // competitor - Stompbox, computed by the backend
}

export interface CandidateMatch {
  candidate_id: string
  stompbox_product_id: string
  competitor: SearchTarget
  product_url: string
  extraction_status: ExtractionStatus
  match_status: MatchStatus
  match_confidence: 'HIGH' | 'MEDIUM' | 'LOW'
  match_reasons: string[]
  review_reasons: string[]
  page: { kind: 'product_page' | 'non_product_page' | 'unknown'; retrieved: boolean; basis: string }
  brand: AttributeCheck
  model: AttributeCheck
  variants: AttributeCheck[]
  condition: {
    condition: 'NEW' | 'USED' | 'REFURBISHED' | 'UNKNOWN'
    comparable_to_new: boolean
    evidence: EvidenceRef[]
    note: string | null
  }
  price: PriceValidation
  comparison: PriceComparison
}

export interface ProductMatchSummary {
  product_id: string
  product_name: string
  brand: string
  manufacturer_model: string
  stompbox_price: string | null
  counts: Record<MatchStatus, number>
  comparable_prices: number
  candidates: CandidateMatch[]
}

// --- Pipeline runs (Phase 7) ---------------------------------------------------

export type RunStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'PARTIALLY_COMPLETED' | 'FAILED' | 'CANCELLED'
export const TERMINAL_RUN_STATUSES: RunStatus[] = ['COMPLETED', 'PARTIALLY_COMPLETED', 'FAILED', 'CANCELLED']
export type StageName =
  | 'validate'
  | 'discovery'
  | 'review_queue'
  | 'targets'
  | 'product_search'
  | 'extraction'
  | 'matching'
export type StageStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'PARTIAL' | 'SKIPPED' | 'FAILED' | 'CANCELLED'
export type ProductRunStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'

export interface StageState {
  name: StageName
  status: StageStatus
  started_at: string | null
  finished_at: string | null
  done: number
  total: number
  message: string | null
  errors: string[]
}

export interface RunRequest {
  product_ids: string[]
  run_discovery: boolean
  domains: string[] | null
}

export interface RunSummary {
  products_total: number
  products_completed: number
  products_failed: number
  candidates: number
  exact_matches: number
  similar_matches: number
  review_required: number
  no_match: number
  comparable_prices: number
}

export interface ExecutionRun {
  run_id: string
  status: RunStatus
  request: RunRequest
  created_at: string
  started_at: string | null
  finished_at: string | null
  cancel_requested: boolean
  stages: StageState[]
  summary: RunSummary
  error: string | null
  warnings: string[]
  last_event_seq: number
}

export interface ExecutionEvent {
  seq: number
  event_id: string
  run_id: string
  type: string
  timestamp: string
  product_id: string | null
  stage: StageName | null
  message: string
  payload: Record<string, unknown>
}

/** Payload of the `competitor_discovered` SSE event. */
export interface CompetitorDiscoveredPayload {
  domain: string
  approval_status: DomainApprovalStatus
  classification_status: CandidateSummary['classification_status']
  company_type: CompanyType | null
  competitor_class?: BackendCompetitorClass | null // absent on events from older backends
  proposed_competitor_class?: BackendCompetitorClass | null
  reviewed: boolean
}

export interface ProductRunResult {
  product_id: string
  status: ProductRunStatus
  error: string | null
  intelligence: ProductIntelligence | null
  matching: ProductMatchSummary | null
}

export interface PipelineResult {
  discovery: DiscoveryRunResult | null
  new_pending_domains: string[]
  search_targets: SearchTarget[]
  skipped_targets: SkippedTarget[]
  intelligence_mode: LlmMode | null
  llm2_disabled_reason: string | null
  intelligence_usage: IntelligenceUsage | null
  products: ProductRunResult[]
}

// Mirrors PIPELINE_MAX_PRODUCTS_PER_RUN (default 5). The backend enforces it (422).
export const MAX_PRODUCTS_PER_RUN = 5

// --- HTTP helpers --------------------------------------------------------------

/** Error with the HTTP status, so callers can tell 404 (expired run) from other failures. */
export class ApiError extends Error {
  readonly status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

function errorMessage(path: string, status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // FastAPI validation errors
    return detail.map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).join('.')}: ${d.msg}`).join('; ')
  }
  if (detail && typeof detail === 'object' && 'message' in detail) return String(detail.message)
  return `${path} returned HTTP ${status}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  })
  const body: unknown = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(errorMessage(path, res.status, body), res.status)
  return body as T
}

const getJson = <T>(path: string) => request<T>(path)
const sendJson = <T>(method: string, path: string, body: unknown) =>
  request<T>(path, { method, body: JSON.stringify(body) })

export const fetchHealth = () => getJson<Health>('/api/health')

export const fetchCandidates = () => getJson<CandidateSummary[]>('/api/review/candidates')

export const fetchCandidate = (domain: string) =>
  getJson<CandidateDetail>(`/api/review/candidates/${encodeURIComponent(domain)}`)

export const approveCandidate = (domain: string, input: DecisionInput) =>
  sendJson<DomainDecision>('POST', `/api/review/candidates/${encodeURIComponent(domain)}/approve`, input)

export const rejectCandidate = (domain: string, input: Omit<DecisionInput, 'benchmark_eligible'>) =>
  sendJson<DomainDecision>('POST', `/api/review/candidates/${encodeURIComponent(domain)}/reject`, input)

export const updateDecision = (domain: string, input: DecisionInput & { decision: ReviewDecision }) =>
  sendJson<DomainDecision>('PUT', `/api/review/decisions/${encodeURIComponent(domain)}`, input)

export const runDiscovery = (productIds: string[]) =>
  sendJson<DiscoveryRunSummary>('POST', '/api/discovery/runs', { product_ids: productIds })

export const fetchProducts = () => getJson<ProductListResponse>('/api/catalogue/products')

export const fetchCompetitors = () => getJson<CompetitorDomain[]>('/api/competitors')

export const fetchDiscoveryConfig = () => getJson<DiscoveryConfig>('/api/discovery/config')

export const fetchIntelligenceConfig = () => getJson<IntelligenceConfig>('/api/intelligence/config')

export const startRun = (body: RunRequest) => sendJson<ExecutionRun>('POST', '/api/runs', body)

export const listRuns = () => getJson<ExecutionRun[]>('/api/runs')

export const fetchRun = (runId: string) => getJson<ExecutionRun>(`/api/runs/${encodeURIComponent(runId)}`)

export const fetchRunResult = (runId: string) =>
  getJson<PipelineResult>(`/api/runs/${encodeURIComponent(runId)}/result`)

export const fetchRunEvents = (runId: string, after: number) =>
  getJson<ExecutionEvent[]>(`/api/runs/${encodeURIComponent(runId)}/events?after=${after}`)

export const cancelRun = (runId: string) =>
  sendJson<ExecutionRun>('POST', `/api/runs/${encodeURIComponent(runId)}/cancel`, {})

export const runStreamUrl = (runId: string) => `/api/runs/${encodeURIComponent(runId)}/stream`
