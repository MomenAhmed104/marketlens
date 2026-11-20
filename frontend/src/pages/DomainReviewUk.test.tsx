// UK relevance evidence on the Domain Review page (Option B). The backend verifies the quotes
// and says whether the evidence is sufficient, contradicted or mixed; the page shows it in plain
// English, keeps verified evidence apart from unverified claims, and has no UK category override.
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import App from '../App'
import type { CandidateDetail, CandidateSummary, UkQuote, UkReview } from '../api'
import { at, fixture, installEventSource, makeCandidate, mockAppApi, type Call } from '../test/fakes'

const RUN_ID = fixture().run.run_id

const quote = (kind: UkQuote['kind'], text: string, verified = true, url = 'https://shop.example/about'): UkQuote => ({
  url,
  quote: text,
  kind,
  verified,
  supports: verified,
  note: verified ? null : 'not found verbatim in the stored title or snippet of that URL',
})

const review = (over: Partial<UkReview>): UkReview => ({
  assessed: true,
  sufficient: false,
  contradicted: false,
  mixed_location: false,
  independent_sources: 0,
  summary: 'Insufficient verified UK evidence: nothing verified shows a UK shop, address or company. Needs your review.',
  reasons: ['Insufficient verified UK evidence'],
  missing_evidence: [],
  verified_evidence: [],
  unverified_claims: [],
  ...over,
})

const VERIFIED = review({
  sufficient: true,
  independent_sources: 2,
  summary: 'Verified UK relevance evidence from 2 independent page(s). Approval is still a reviewer decision.',
  verified_evidence: [
    quote('UK_PHYSICAL_STORE', 'Visit our shop in Bedford'),
    quote('UK_DELIVERY', 'We deliver to all UK mainland addresses', true, 'https://shop.example/delivery'),
  ],
  unverified_claims: [quote('UK_PHYSICAL_STORE', 'Our flagship store on Oxford Street', false)],
})

const CONTRADICTED = review({
  contradicted: true,
  summary: 'Contradictory UK evidence: a verified quote says it does not deliver to or serve the UK. Needs your review.',
  verified_evidence: [quote('NO_UK_SERVICE', 'We do not ship to the United Kingdom')],
})

const INSUFFICIENT = review({
  missing_evidence: ['For a UK-based business: a shop or showroom customers can visit, a UK address with a postcode'],
  unverified_claims: [quote('WEAK_SIGNAL', '£189.00 in stock', false)],
})

const MIXED = review({
  sufficient: true,
  mixed_location: true,
  independent_sources: 1,
  summary: 'Verified UK presence (1 independent page(s)), but also a verified quote that the business is based outside the UK.',
})

const withUk = (domain: string, uk: UkReview | null, over: Partial<CandidateSummary> = {}): CandidateSummary => ({
  ...makeCandidate(domain),
  uk_review: uk,
  ...over,
})

describe('Domain Review: UK relevance evidence', () => {
  let calls: Call[]
  beforeEach(() => {
    installEventSource()
    localStorage.setItem('stompbox.reviewer', 'Sam')
  })

  const open = async (candidates: CandidateSummary[]) => {
    calls = mockAppApi({
      candidates,
      override: (m, p) => {
        const c = candidates.find((x) => p === `/api/review/candidates/${x.domain}`)
        if (m === 'GET' && c) {
          const detail: CandidateDetail = {
            ...c,
            evidence: [],
            cited_evidence_urls: [],
            relevant_categories: [],
            rationale: null,
            automatic_reasons: [],
            validation_notes: [],
            history: [],
          }
          return { body: detail }
        }
        if (m === 'POST' && p.endsWith('/approve'))
          return { body: { ...makeCandidate(candidates[0].domain, 'APPROVED').decision } }
        return undefined
      },
    })
    at(`?run=${RUN_ID}`, '#/domains')
    render(<App />)
    await screen.findByRole('tablist', { name: 'Domain sections' })
  }
  const card = async (domain: string) => (await screen.findByRole('link', { name: domain })).closest('li')!
  const ukOf = async (domain: string) => within(await card(domain)).getByLabelText('UK relevance')

  it('shows what the verified evidence says on each card, with no UK category', async () => {
    await open([withUk('ukshop.example', VERIFIED), withUk('usshop.example', CONTRADICTED),
      withUk('unsure.example', INSUFFICIENT), withUk('mixed.example', MIXED), withUk('legacy.example', null)])
    const uk = await ukOf('ukshop.example')
    expect(uk).toHaveTextContent('Verified UK evidence')
    expect(uk).toHaveTextContent('From 2 independent pages')
    expect(await ukOf('usshop.example')).toHaveTextContent('Contradictory UK evidence')
    expect(await ukOf('unsure.example')).toHaveTextContent('Insufficient UK evidence')
    expect(await ukOf('mixed.example')).toHaveTextContent('UK evidence: check location')
    expect(await ukOf('mixed.example')).toHaveTextContent('From 1 independent page')
    const legacy = await ukOf('legacy.example')
    expect(legacy).toHaveTextContent('UK evidence not assessed')
    expect(legacy).toHaveTextContent('No verified UK evidence yet')
    for (const d of ['ukshop.example', 'usshop.example', 'unsure.example']) {
      expect(await ukOf(d)).not.toHaveTextContent(/UK-based|serves UK customers|confidence|Eligible/)
    }
  })

  it('keeps verified evidence apart from unverified claims', async () => {
    const user = userEvent.setup()
    await open([withUk('ukshop.example', VERIFIED)])
    const c = await card('ukshop.example')
    await user.click(within(c).getByRole('button', { name: /Show evidence/ }))
    const verified = await within(c).findByRole('list', { name: 'Verified UK evidence' })
    expect(within(verified).getAllByRole('listitem')).toHaveLength(2)
    expect(verified).toHaveTextContent('UK shop or showroom')
    expect(verified).toHaveTextContent('“We deliver to all UK mainland addresses”')
    expect(verified).toHaveTextContent('Verified on the page')
    expect(within(verified).getAllByRole('link')[0]).toHaveAttribute('href', 'https://shop.example/about')
    const claims = within(c).getByRole('list', { name: 'Unverified UK claims' })
    expect(claims).toHaveTextContent('Our flagship store on Oxford Street')
    expect(claims).toHaveTextContent('Not verified')
    expect(claims).not.toHaveTextContent('Verified on the page')
    expect(verified).not.toHaveTextContent('Oxford Street')
  })

  it('explains what is missing for an uncertain domain', async () => {
    const user = userEvent.setup()
    await open([withUk('unsure.example', INSUFFICIENT)])
    const c = await card('unsure.example')
    await user.click(within(c).getByRole('button', { name: /Show evidence/ }))
    expect(await within(c).findByRole('list', { name: 'Missing UK evidence' })).toHaveTextContent(
      'a UK address with a postcode',
    )
    expect(within(c).getByText('No verified UK evidence.')).toBeInTheDocument()
    expect(within(c).getByRole('list', { name: 'Unverified UK claims' })).toHaveTextContent('Weak signal')
  })

  it('has no UK override control and sends no UK fields with a decision', async () => {
    const user = userEvent.setup()
    await open([withUk('ukshop.example', VERIFIED)])
    const c = await card('ukshop.example')
    await user.click(within(c).getByRole('button', { name: 'Approve…' }))
    expect(within(c).queryByLabelText(/override/i)).toBeNull()
    await user.selectOptions(within(c).getByLabelText(/Company type/), 'retailer')
    await user.type(within(c).getByLabelText(/Notes/), 'Shop visited in person')
    await user.click(within(c).getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(calls.some((x) => x.path.endsWith('/approve'))).toBe(true))
    const body = calls.find((x) => x.path.endsWith('/approve'))!.body as Record<string, unknown>
    expect(body).toMatchObject({ reviewer: 'Sam', company_type: 'retailer', notes: 'Shop visited in person',
      benchmark_eligible: false })
    expect(Object.keys(body).filter((k) => k.startsWith('uk_'))).toEqual([])
  })

  it('lets a reviewer approve despite contradictory evidence and keeps the contradiction in view', async () => {
    const user = userEvent.setup()
    await open([withUk('usshop.example', CONTRADICTED)])
    const c = await card('usshop.example')
    await user.click(within(c).getByRole('button', { name: 'Approve…' }))
    expect(within(c).getByText(/the contradiction stays on record/)).toBeInTheDocument()
    await user.selectOptions(within(c).getByLabelText(/Company type/), 'retailer')
    await user.type(within(c).getByLabelText(/Notes/), 'Confirmed by phone that they ship to the UK')
    await user.click(within(c).getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(calls.some((x) => x.path.endsWith('/approve'))).toBe(true))
    expect(calls.find((x) => x.path.endsWith('/approve'))!.body).toMatchObject({
      notes: 'Confirmed by phone that they ship to the UK',
      benchmark_eligible: false,
    })
  })

  it('warns when approving without sufficient verified evidence, but does not block it', async () => {
    const user = userEvent.setup()
    await open([withUk('unsure.example', INSUFFICIENT)])
    const c = await card('unsure.example')
    await user.click(within(c).getByRole('button', { name: 'Approve…' }))
    expect(within(c).getByText(/no sufficient verified UK evidence/)).toBeInTheDocument()
    await user.selectOptions(within(c).getByLabelText(/Company type/), 'retailer')
    expect(within(c).getByRole('button', { name: 'Approve' })).toBeEnabled()
  })
})
