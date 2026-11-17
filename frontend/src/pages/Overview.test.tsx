import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import App from '../App'
import { at, fixture, installEventSource, mockAppApi } from '../test/fakes'

const RUN_ID = fixture().run.run_id

const stat = (labelText: string) =>
  screen.getAllByText(labelText).find((el) => el.classList.contains('stat-label'))!.closest('.stat') as HTMLElement

async function open(result = fixture().result) {
  mockAppApi({ result })
  at(`?run=${RUN_ID}`, '#/overview')
  const view = render(<App />)
  await screen.findByRole('heading', { name: /exact match/ })
  return view
}

describe('Overview page', () => {
  beforeEach(() => {
    installEventSource()
  })

  it('leads with the selected run, its product and the key finding', async () => {
    await open()
    const hero = screen.getByRole('heading', { level: 2, name: '3 exact matches across 14 competitors' }).closest('section')!
    expect(within(hero).getByText('Boss RC-5 Loop Station Guitar Effect Pedal · SB-001')).toBeInTheDocument()
    expect(within(hero).getByText('1 eligible price comparison: Andertons.')).toBeInTheDocument()
    expect(within(hero).getByText(/Every completed product has an exact match/)).toBeInTheDocument()
    expect(within(hero).getByRole('link', { name: 'Open product results' })).toHaveAttribute('href', '#/products/SB-001')
  })

  it('shows key figures from the run results, with their sources', async () => {
    await open()
    expect(within(stat('Products analysed')).getByText('1/1')).toBeInTheDocument()
    expect(within(stat('Competitors searched')).getByText('14')).toBeInTheDocument()
    expect(within(stat('Competitors searched')).getByText('13 configured · 1 auto-approved (this run)')).toBeInTheDocument()
    expect(stat('Needs review')).toHaveClass('stat-attention')
    expect(within(stat('Needs review')).getByText('1 product · 1 candidate')).toBeInTheDocument()
    expect(stat('Eligible prices')).toHaveClass('stat-accent')
    expect(stat('Eligible prices')).toHaveAttribute('title', 'Source: run.summary.comparable_prices')
  })

  it('shows the match distribution with counts only', async () => {
    const { container } = await open()
    const dist = screen.getByRole('heading', { name: 'Match distribution' }).closest('section')!
    const table = within(dist).getByRole('table')
    expect(within(table).getByRole('row', { name: 'Exact match 3' })).toBeInTheDocument()
    expect(within(table).getByRole('row', { name: 'Review required 1' })).toBeInTheDocument()
    expect(within(table).getByRole('row', { name: 'Total 5' })).toBeInTheDocument()
    expect(dist.querySelectorAll('.dist-bar .dist-seg')).toHaveLength(3) // zero counts get no segment
    expect(container.querySelector('main')!.textContent).not.toMatch(/%|saving|discount/i)
  })

  it('makes the eligible comparison prominent and lists coverage', async () => {
    await open()
    const prices = screen.getByRole('heading', { name: 'Price comparisons' }).closest('section')!
    const card = within(prices).getByText('Andertons').closest('li')!
    expect(card).toHaveTextContent('£189.00')
    expect(card).toHaveTextContent('£189.99')
    expect(card).toHaveTextContent('−£0.99')
    expect(card).toHaveTextContent('competitor lower')
    expect(within(prices).getAllByRole('listitem')).toHaveLength(1)

    const coverage = screen.getByRole('heading', { name: 'Competitor coverage' }).closest('section')!
    expect(within(coverage).getAllByRole('row')).toHaveLength(1 + 4) // header + competitors with candidates
    expect(within(coverage).getByText('gibson.com').closest('tr')).toHaveTextContent('Not a benchmark')
    expect(within(coverage).getByText(/Searched with no candidates \(10\)/)).toBeInTheDocument()
  })

  it('explains why there is no price comparison when extraction is off', async () => {
    const f = fixture()
    f.result.intelligence_mode = 'LLM_DISABLED'
    f.result.llm2_disabled_reason = 'LLM2_MODEL not set'
    for (const c of f.result.products[0].matching!.candidates) {
      c.comparison = { ...c.comparison, eligible: false, difference_gbp: null }
      c.price = { ...c.price, status: 'NOT_EXTRACTED' }
    }
    await open(f.result)
    expect(screen.getByText(/No price comparison: LLM 2 extraction is off/)).toBeInTheDocument()
    const prices = screen.getByRole('heading', { name: 'Price comparisons' }).closest('section')!
    expect(within(prices).getByText('No eligible price comparison in this run')).toBeInTheDocument()
    const steps = within(prices).getAllByRole('listitem')
    expect(steps[0]).toHaveTextContent('Exact matches found: met')
    expect(steps[1]).toHaveTextContent('Competitor prices extracted: not met')
    expect(steps[1]).toHaveTextContent('LLM 2 extraction is off (LLM2_MODEL not set)')
  })

  it('lists products without an exact match', async () => {
    const f = fixture()
    f.result.products[0].matching!.candidates = []
    f.result.products[0].matching!.counts.EXACT_MATCH = 0
    mockAppApi({ result: f.result, run: { ...f.run, summary: { ...f.run.summary, exact_matches: 0 } } })
    at(`?run=${RUN_ID}`, '#/overview')
    render(<App />)
    const attention = (await screen.findByRole('heading', { name: 'Needs attention' })).closest('section')!
    expect(within(attention).getByText(/No candidate pages on the searched domains/)).toBeInTheDocument()
    expect(within(attention).getByText(/This does not mean it is unavailable/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'No exact match across 14 competitors' })).toBeInTheDocument()
  })

  it('has an empty state without a run and for an expired run', async () => {
    mockAppApi({ runs: [] })
    at('', '#/overview')
    const { unmount } = render(<App />)
    await screen.findByRole('heading', { level: 1, name: 'Overview' })
    expect(within(screen.getByRole('main')).getByText('No run selected')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Start or open a run' })).toHaveAttribute('href', '#/runs')
    unmount()

    mockAppApi({ run: null, runs: [] })
    at('?run=run-gone', '#/overview')
    render(<App />)
    expect(await screen.findByText('This run is no longer available')).toBeInTheDocument()
  })
})
