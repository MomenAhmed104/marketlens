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

// --- HTTP helpers --------------------------------------------------------------

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
  if (!res.ok) throw new Error(errorMessage(path, res.status, body))
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
