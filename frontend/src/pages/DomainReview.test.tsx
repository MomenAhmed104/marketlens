import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import App from '../App'
import type { CandidateDetail, CandidateSummary, EvidenceItem } from '../api'
import { at, fixture, installEventSource, makeCandidate, mockAppApi, type Call } from '../test/fakes'

const RUN_ID = fixture().run.run_id

describe('Domain Review page', () => {
  let calls: Call[]
  beforeEach(() => {
    installEventSource()
    localStorage.setItem('stompbox.reviewer', 'Sam')
  })

  const open = async (candidates = [makeCandidate('newshop.example'), makeCandidate('bad.example', 'REJECTED')]) => {
    calls = mockAppApi({
      candidates,
      override: (m, p) =>
        m === 'POST' && p.endsWith('/approve')
          ? { body: { ...makeCandidate('newshop.example', 'APPROVED').decision } }
          : undefined,
    })
    at(`?run=${RUN_ID}`, '#/domains')
    render(<App />)
    return screen.findByRole('tablist', { name: 'Domain sections' })
  }

  it('keeps the approval workflow: pending domains can be approved', async () => {
    const user = userEvent.setup()
    await open()
    expect(screen.getByRole('tab', { name: /Pending/ })).toHaveAttribute('aria-selected', 'true')
    const card = (await screen.findByRole('link', { name: 'newshop.example' })).closest('li')!
    await user.click(within(card).getByRole('button', { name: 'Approve…' }))
    await user.selectOptions(within(card).getByLabelText(/Company type/), 'retailer')
    await user.click(within(card).getByRole('button', { name: 'Approve' }))
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && c.path === '/api/review/candidates/newshop.example/approve')).toBe(true),
    )
    const approve = calls.find((c) => c.path.endsWith('/approve'))!
    expect(approve.body).toMatchObject({ reviewer: 'Sam', company_type: 'retailer' })
    // the app refreshes the queue (sidebar count, search targets) after a decision
    await waitFor(() => expect(calls.filter((c) => c.path === '/api/intelligence/config').length).toBeGreaterThan(1))
  })

  const classified = (domain: string, over: Partial<CandidateSummary> = {}): CandidateSummary => ({
    ...makeCandidate(domain),
    discovery_mode: 'LLM_ENABLED',
    classification_status: 'COMPLETED',
    llm_classified: true,
    name_verified: true,
    proposed_company_name: `Shop ${domain}`,
    proposed_company_type: 'retailer',
    relevance: 0.93,
    confidence: 0.97,
    uk_seller: 'yes',
    evidence_count: 2,
    ...over,
  })

  const evidence = (url: string, kind: EvidenceItem['kind'], snippet: string): EvidenceItem => ({
    url,
    canonical_url: url,
    hostname: 'direct.example',
    title: null,
    snippet,
    kind,
    query_text: 'q',
    product_id: kind === 'discovery_result' ? 'SB-001' : null,
    provider: 'exa',
    provider_request_id: null,
    retrieved_at: '2026-11-14T19:53:26Z',
    mentions_product_model: kind === 'discovery_result',
    mentions_brand: kind === 'discovery_result',
  })

  it('shows each backend classification, and Needs Review when classification is missing', async () => {
    await open([
      classified('direct.example', { competitor_class: 'DIRECT_COMPETITOR', proposed_competitor_class: 'DIRECT_COMPETITOR' }),
      classified('related.example', { proposed_company_type: 'marketplace', competitor_class: 'RELATED_RETAILER' }),
      classified('review.example', { competitor_class: 'NEEDS_REVIEW', proposed_competitor_class: 'DIRECT_COMPETITOR' }),
      makeCandidate('unclassified.example'),
    ])
    const card = async (d: string) => (await screen.findByRole('link', { name: d })).closest('li')!
    const cls = async (d: string) => within(await card(d)).getByTestId('competitor-class')
    expect(await cls('direct.example')).toHaveTextContent('Direct Competitor')
    expect(await cls('direct.example')).toHaveClass('class-direct')
    expect(await cls('related.example')).toHaveTextContent('Related Retailer')
    expect(await cls('related.example')).toHaveClass('class-related')
    expect(await cls('review.example')).toHaveTextContent('Needs Review') // the final class, not the proposal
    expect(await cls('review.example')).toHaveClass('class-needs-review')
    for (const d of ['direct.example', 'related.example', 'review.example']) {
      expect(await cls(d)).toHaveAttribute('data-source', 'backend')
      expect(await cls(d)).not.toHaveAttribute('title')
    }
    expect(await cls('unclassified.example')).toHaveTextContent('Needs Review')
    expect(await cls('unclassified.example')).toHaveClass('class-needs-review')
    expect(await cls('unclassified.example')).toHaveAttribute('data-source', 'fallback')
    expect(await cls('unclassified.example')).toHaveAttribute('title', expect.stringMatching(/provisional/))
    expect(within(await card('direct.example')).getByTitle('Company type')).toHaveTextContent(
      'retailer (LLM proposal)',
    )
    expect(screen.queryByText(/Less Relevant/i)).not.toBeInTheDocument()
  })

  it('backend class wins over the fallback in both directions', async () => {
    await open([
      // fallback would say Related (known marketplace)
      { ...makeCandidate('ebay.fr'), known_marketplace: true, competitor_class: 'NEEDS_REVIEW' },
      // fallback would say Needs Review (no LLM data)
      { ...makeCandidate('nollm.example'), competitor_class: 'DIRECT_COMPETITOR' },
    ])
    const cls = async (d: string) =>
      within((await screen.findByRole('link', { name: d })).closest('li')!).getByTestId('competitor-class')
    expect(await cls('ebay.fr')).toHaveTextContent('Needs Review')
    expect(await cls('nollm.example')).toHaveTextContent('Direct Competitor')
  })

  it('older records without a class: high relevance never makes a retailer Direct; manufacturers and directories are Needs Review', async () => {
    await open([
      classified('highscore.example', { relevance: 0.99, confidence: 0.99 }),
      classified('threshold.example', { relevance: 0.75, proposed_company_type: 'specialist_retailer' }),
      classified('maker.example', { proposed_company_type: 'manufacturer' }),
      classified('compare.example', { proposed_company_type: 'directory' }),
    ])
    for (const d of ['highscore.example', 'threshold.example', 'maker.example', 'compare.example']) {
      const pill = within((await screen.findByRole('link', { name: d })).closest('li')!).getByTestId('competitor-class')
      expect(pill).toHaveTextContent('Needs Review')
      expect(pill).toHaveAttribute('data-source', 'fallback')
    }
    expect(screen.queryByText('Direct Competitor')).not.toBeInTheDocument()
  })

  it('titles unnamed domains by the domain, without repeating unknown details', async () => {
    await open([makeCandidate('billybowmanmusic.co.uk')])
    const title = await screen.findByRole('link', { name: 'billybowmanmusic.co.uk' })
    expect(title).toHaveClass('domain-name')
    const card = title.closest('li')!
    expect(within(card).getAllByText('billybowmanmusic.co.uk')).toHaveLength(1)
    expect(within(card).getByTestId('competitor-class')).toHaveTextContent('Needs Review')
    expect(card).not.toHaveTextContent(/unknown/i)
    expect(within(card).queryByTitle('Company type')).not.toBeInTheDocument()
  })

  it('classifies a marketplace as Related Retailer, never Direct, even if LLM 1 said retailer', async () => {
    await open([
      classified('amazon.co.uk', { known_marketplace: true, relevance: 0.99 }),
      { ...makeCandidate('ebay.fr'), known_marketplace: true },
    ])
    const amazon = (await screen.findByRole('link', { name: 'amazon.co.uk' })).closest('li')!
    expect(within(amazon).getByTestId('competitor-class')).toHaveTextContent('Related Retailer')
    const ebay = (await screen.findByRole('link', { name: 'ebay.fr' })).closest('li')!
    expect(within(ebay).getByTestId('competitor-class')).toHaveTextContent('Related Retailer')
    expect(within(ebay).getByTitle('Company type')).toHaveTextContent('marketplace (known marketplace domain)')
    expect(screen.queryByText('Direct Competitor')).not.toBeInTheDocument()
  })

  it('shows only the simplified entry: no scores, rationale or eligibility; evidence on demand', async () => {
    const user = userEvent.setup()
    const detail: CandidateDetail = {
      ...classified('direct.example'),
      evidence: [
        evidence('https://direct.example/about', 'domain_profile', 'Independent UK music shop'),
        evidence('https://direct.example/boss-rc-5', 'discovery_result', 'Our price £209 In stock'),
      ],
      cited_evidence_urls: [],
      relevant_categories: ['Effects'],
      rationale: 'RATIONALE TEXT',
      automatic_reasons: ['Needs approval: 1 evidence URL(s) < 2'],
      validation_notes: [],
      history: [],
    }
    calls = mockAppApi({
      candidates: [classified('direct.example')],
      override: (m, p) => (m === 'GET' && p === '/api/review/candidates/direct.example' ? { body: detail } : undefined),
    })
    at(`?run=${RUN_ID}`, '#/domains')
    render(<App />)
    const card = (await screen.findByRole('link', { name: 'direct.example' })).closest('li')!
    expect(card).toHaveTextContent('Shop direct.example')
    expect(card).toHaveTextContent('Pending review')
    for (const hidden of [/relevance/i, /confidence/i, /0\.93/, /0\.97/, /rationale/i, /benchmark/i, /targeted searches/i]) {
      expect(card).not.toHaveTextContent(hidden)
    }
    await user.click(within(card).getByRole('button', { name: 'Show evidence (2)' }))
    const list = await within(card).findByRole('list', { name: 'Evidence' })
    const hrefs = within(list).getAllByRole('link').map((a) => a.getAttribute('href'))
    expect(hrefs).toEqual(['https://direct.example/boss-rc-5', 'https://direct.example/about']) // product page first
    expect(list).toHaveTextContent('Our price £209 In stock')
    expect(card).not.toHaveTextContent('RATIONALE TEXT')
    expect(card).not.toHaveTextContent('Needs approval: 1 evidence URL')
  })

  it('keeps the rejection workflow', async () => {
    const user = userEvent.setup()
    await open()
    const card = (await screen.findByRole('link', { name: 'newshop.example' })).closest('li')!
    await user.click(within(card).getByRole('button', { name: 'Reject…' }))
    await user.click(within(card).getByRole('button', { name: 'Reject' }))
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && c.path === '/api/review/candidates/newshop.example/reject')).toBe(true),
    )
    expect(calls.find((c) => c.path.endsWith('/reject'))!.body).toMatchObject({ reviewer: 'Sam', company_type: null })
  })

  it('shows the decision on approved domains and keeps Change decision', async () => {
    const approved = { ...makeCandidate('market.example', 'APPROVED'), search_eligible: true, benchmark_eligible: false }
    await open([approved])
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: /Approved/ }))
    const card = (await screen.findByRole('link', { name: 'market.example' })).closest('li')!
    expect(card).toHaveClass('state-approved')
    expect(within(card).getByText('Approved')).toBeInTheDocument()
    expect(card).toHaveTextContent('Approved by Sam')
    expect(within(card).getByTestId('competitor-class')).toHaveTextContent('Needs Review')
    expect(within(card).getByRole('button', { name: 'Change decision…' })).toBeInTheDocument()
  })

  it('lists configured competitors with benchmark eligibility', async () => {
    const user = userEvent.setup()
    await open()
    await user.click(screen.getByRole('tab', { name: /Configured/ }))
    const panel = screen.getByRole('tabpanel')
    const gibson = within(panel).getByText('Gibson').closest('li')!
    expect(gibson).toHaveTextContent('Reference only, not a benchmark')
    const andertons = within(panel).getByText('Andertons').closest('li')!
    expect(andertons).toHaveTextContent('Benchmark')
    expect(andertons).not.toHaveTextContent('not a benchmark')
  })

  it('shows rejected domains, and run-scoped excluded domains', async () => {
    const user = userEvent.setup()
    await open()
    await user.click(screen.getByRole('tab', { name: /Rejected/ }))
    expect(await within(screen.getByRole('tabpanel')).findByRole('link', { name: 'bad.example' })).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: /Excluded and skipped/ }))
    const panel = screen.getByRole('tabpanel')
    const excluded = fixture().result.discovery!.excluded
    expect(within(panel).getByText(/Excluded by discovery rules in the selected run/)).toHaveTextContent(`(${excluded.length})`)
    expect(within(panel).getByText(/These are not stored/)).toBeInTheDocument()
  })

  it('supports arrow keys between sections', async () => {
    const user = userEvent.setup()
    await open()
    screen.getByRole('tab', { name: /Pending/ }).focus()
    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: /Approved/ })).toHaveFocus()
    expect(screen.getByRole('tab', { name: /Approved/ })).toHaveAttribute('aria-selected', 'true')
    await user.keyboard('{ArrowLeft}{ArrowLeft}')
    expect(screen.getByRole('tab', { name: /Excluded/ })).toHaveFocus()
  })

  it('summarises what the selected run searched and left pending', async () => {
    await open()
    const run = (await screen.findByRole('heading', { name: 'Selected run' })).closest('section')!
    expect(within(run).getByText(/pending review and were/)).toHaveTextContent('ebay.co.uk')
    expect(within(run).getByText('Searched in this run (14)')).toBeInTheDocument()
    expect(within(run).getByText('All domains discovered in this run (4)')).toBeInTheDocument()
  })

  it("shows the run's backend classification for each discovered domain", async () => {
    await open()
    const run = (await screen.findByRole('heading', { name: 'Selected run' })).closest('section')!
    const row = (d: string) => within(run).getByRole('rowheader', { name: d }).closest('tr')!
    const discovered = fixture().result.discovery!.candidates
    expect(discovered.every((c) => c.competitor_class)).toBe(true) // the fixture follows the backend schema
    expect(within(row('ebay.co.uk')).getByTestId('competitor-class')).toHaveTextContent('Related Retailer')
    expect(within(row('guitarworld.com')).getByTestId('competitor-class')).toHaveTextContent('Needs Review')
  })

  const approveForm = async (type: string) => {
    const user = userEvent.setup()
    await open()
    const card = (await screen.findByRole('link', { name: 'newshop.example' })).closest('li')!
    await user.click(within(card).getByRole('button', { name: 'Approve…' }))
    await user.selectOptions(within(card).getByLabelText(/Company type/), type)
    return { user, card, box: within(card).getByRole('checkbox', { name: /Use as a price benchmark/ }) }
  }

  it.each(['manufacturer', 'retailer', 'specialist_retailer'])(
    'lets a %s be approved as a price benchmark',
    async (type) => {
      const { user, card, box } = await approveForm(type)
      expect(box).toBeEnabled()
      await user.click(box)
      expect(box).toBeChecked()
      await user.click(within(card).getByRole('button', { name: 'Approve' }))
      await waitFor(() => expect(calls.some((c) => c.path.endsWith('/approve'))).toBe(true))
      expect(calls.find((c) => c.path.endsWith('/approve'))!.body).toMatchObject({
        company_type: type,
        benchmark_eligible: true,
      })
    },
  )

  it.each(['marketplace', 'directory', 'other', '__unknown__'])(
    'never offers benchmarking for type %s',
    async (type) => {
      const { user, card, box } = await approveForm(type)
      expect(box).toBeDisabled()
      expect(box).not.toBeChecked()
      expect(card).toHaveTextContent('(retailer or manufacturer types only)')
      await user.click(within(card).getByRole('button', { name: 'Approve' }))
      await waitFor(() => expect(calls.some((c) => c.path.endsWith('/approve'))).toBe(true))
      expect(calls.find((c) => c.path.endsWith('/approve'))!.body).toMatchObject({ benchmark_eligible: false })
    },
  )

  it('drops a ticked benchmark box when the type changes to an ineligible one', async () => {
    const { user, card, box } = await approveForm('manufacturer')
    await user.click(box)
    await user.selectOptions(within(card).getByLabelText(/Company type/), 'marketplace')
    expect(box).toBeDisabled()
    expect(box).not.toBeChecked()
  })
})
