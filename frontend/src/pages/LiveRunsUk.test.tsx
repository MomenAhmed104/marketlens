// UK relevance evidence in Live Runs (Stage 6.3.3). The event feed shows the backend's UK
// evidence status as soon as a `competitor_discovered` event arrives (flags only); the run
// result supplies the quotes, shown per discovered domain with verified evidence kept apart
// from unverified claims. Nothing here approves a domain or grants benchmark permission.
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import type { ExecutionRun, PipelineResult, UkQuote, UkReview } from '../api'
import App from '../App'
import { at, fixture, installEventSource, mockAppApi, type FakeEventSource } from '../test/fakes'

const F = fixture()
const RUN_ID = F.run.run_id
const running = (): ExecutionRun => ({ ...fixture().run, status: 'RUNNING', finished_at: null })

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
  summary: 'Insufficient verified UK evidence. Needs your review.',
  reasons: ['Insufficient verified UK evidence'],
  missing_evidence: [],
  verified_evidence: [],
  unverified_claims: [],
  ...over,
})

/** The fixture's run result with chosen UK views per discovered domain (undefined = field absent). */
function resultWith(views: Record<string, UkReview | undefined>): PipelineResult {
  const r = fixture().result
  r.discovery!.candidates = r.discovery!.candidates.map((c) => {
    if (!(c.domain in views)) return c
    const { uk_review: _old, ...rest } = c
    return views[c.domain] === undefined ? rest : { ...rest, uk_review: views[c.domain] }
  })
  return r
}

const VERIFIED = review({
  sufficient: true,
  independent_sources: 2,
  summary: 'Verified UK relevance evidence from 2 independent page(s). Approval is still a reviewer decision.',
  verified_evidence: [
    quote('UK_PHYSICAL_STORE', 'Visit our shop in Bedford'),
    quote('UK_DELIVERY', 'We deliver to all UK mainland addresses', true, 'https://shop.example/delivery'),
  ],
  unverified_claims: [quote('UK_ADDRESS', 'Our flagship store on Oxford Street', false)],
})

const CONTRADICTED = review({
  contradicted: true,
  summary: 'Contradictory UK evidence: a verified quote says it does not deliver to or serve the UK. Needs your review.',
  verified_evidence: [quote('NO_UK_SERVICE', 'We do not ship to the United Kingdom')],
  reasons: ['Contradiction: a verified quote says the UK is not delivered to or served'],
})

const row = (domain: string) =>
  within(screen.getByRole('region', { name: 'Discovered domains and UK evidence' }))
    .getByRole('rowheader', { name: domain })
    .closest('tr')!

describe('Live Runs: UK evidence', () => {
  let sources: FakeEventSource[]
  beforeEach(() => {
    sources = installEventSource()
  })

  it('lists each discovered domain with verified quotes, their kinds and unverified claims kept apart', async () => {
    const user = userEvent.setup()
    mockAppApi({
      result: resultWith({ 'beckettsmusic.co.uk': VERIFIED, 'guitarworld.com': CONTRADICTED, 'ebay.co.uk': undefined }),
    })
    at(`?run=${RUN_ID}`, '#/runs')
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Discovered domains and UK evidence' })).toBeInTheDocument()

    const beckett = row('beckettsmusic.co.uk')
    expect(within(beckett).getByText('Verified UK evidence')).toBeInTheDocument()
    expect(within(beckett).getByText('From 2 independent pages')).toBeInTheDocument()
    await user.click(within(beckett).getByText('UK evidence (2 verified, 1 unverified)'))
    const verified = within(beckett).getByRole('list', { name: 'Verified UK evidence' })
    expect(within(verified).getByText('“Visit our shop in Bedford”')).toBeInTheDocument()
    expect(within(verified).getByText('UK shop or showroom')).toBeInTheDocument()
    expect(within(verified).getByText('Delivers to the UK')).toBeInTheDocument()
    expect(within(verified).getAllByText('Verified on the page')).toHaveLength(2)
    const claims = within(beckett).getByRole('list', { name: 'Unverified UK claims' })
    expect(within(claims).getByText('“Our flagship store on Oxford Street”')).toBeInTheDocument()
    expect(within(claims).getByText('Not verified')).toBeInTheDocument()
    expect(within(verified).queryByText(/Oxford Street/)).not.toBeInTheDocument() // never mixed with evidence

    const world = row('guitarworld.com')
    expect(within(world).getByText('Contradictory UK evidence')).toBeInTheDocument()
    await user.click(within(world).getByText('UK evidence (1 verified, 0 unverified)'))
    expect(within(world).getByText(/a verified quote says it does not deliver to or serve the UK/)).toBeInTheDocument()
    expect(within(world).getByText('Does not serve the UK')).toBeInTheDocument()
    expect(within(world).getByText('Contradiction: a verified quote says the UK is not delivered to or served')).toBeInTheDocument()

    // No UK view in the payload (older backend): "not assessed", with no invented evidence.
    const ebay = row('ebay.co.uk')
    expect(within(ebay).getByText('UK evidence not assessed')).toBeInTheDocument()
    expect(within(ebay).queryByText(/UK evidence \(/)).not.toBeInTheDocument()
    expect(within(ebay).queryByText('Verified on the page')).not.toBeInTheDocument()
  })

  it('shows no discovered-domains section when the run has no discovery result', async () => {
    const r = fixture().result
    r.discovery = null
    mockAppApi({ result: r })
    at(`?run=${RUN_ID}`, '#/runs')
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Event feed' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Discovered domains and UK evidence' })).not.toBeInTheDocument()
  })

  it('shows the UK evidence status of each domain in the event feed as soon as its event arrives', async () => {
    mockAppApi({ run: running() })
    at(`?run=${RUN_ID}`, '#/runs')
    render(<App />)
    await waitFor(() => expect(sources).toHaveLength(1))
    const discovered = F.events.filter((e) => e.type === 'competitor_discovered')
    expect(discovered.every((e) => typeof e.payload.uk_assessed === 'boolean')).toBe(true) // backend payload
    const upTo = Math.max(...discovered.map((e) => e.seq))
    const events = F.events
      .filter((e) => e.seq <= upTo)
      .map((e) => {
        if (e.type !== 'competitor_discovered') return e
        const d = e.payload.domain
        if (d === 'guitarworld.com') return { ...e, payload: { ...e.payload, uk_evidence_contradicted: true } }
        if (d === 'ebay.co.uk') return { ...e, payload: { ...e.payload, uk_assessed: false, uk_evidence_sufficient: false } }
        if (d === 'lookalike-music.co.uk') {
          const { uk_assessed: _a, uk_evidence_sufficient: _s, uk_evidence_contradicted: _c, uk_mixed_location: _m,
            uk_independent_sources: _n, ...older } = e.payload
          return { ...e, payload: older } // an event from an older backend: no UK fields
        }
        return e
      })
    act(() => {
      sources[0].open()
      events.forEach((e) => sources[0].emit(e))
    })
    const item = (domain: string) =>
      screen.getByText((text) => text.startsWith(`Discovered ${domain} (`)).closest<HTMLElement>('.feed-item')!
    expect(within(item('beckettsmusic.co.uk')).getByTestId('uk-evidence')).toHaveTextContent('Verified UK evidence')
    expect(within(item('beckettsmusic.co.uk')).getByTestId('uk-evidence')).toHaveTextContent('From 2 independent pages')
    expect(within(item('guitarworld.com')).getByTestId('uk-evidence')).toHaveTextContent('Contradictory UK evidence')
    expect(within(item('ebay.co.uk')).getByTestId('uk-evidence')).toHaveTextContent('UK evidence not assessed')
    expect(within(item('lookalike-music.co.uk')).queryByTestId('uk-evidence')).not.toBeInTheDocument()
  })
})
