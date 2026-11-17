import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import type { CandidateMatch } from '../api'
import App from '../App'
import { at, fixture, installEventSource, mockAppApi } from '../test/fakes'

const RUN_ID = fixture().run.run_id
const rowOf = (name: string) => screen.getByRole('link', { name }).closest('tr')!

async function open(result = fixture().result) {
  mockAppApi({ result })
  at(`?run=${RUN_ID}`, '#/products/SB-001')
  const view = render(<App />)
  await screen.findByRole('heading', { name: 'Competitor comparison' })
  return view
}

describe('Product Intelligence page', () => {
  beforeEach(() => {
    installEventSource()
  })

  it('shows the product reference and its match counts', async () => {
    await open()
    const ref = screen.getByRole('heading', { name: 'Boss RC-5 Loop Station Guitar Effect Pedal' }).closest('section')!
    expect(within(ref).getByText('SB-001')).toBeInTheDocument()
    expect(within(ref).getByText('£189.99')).toBeInTheDocument()
    expect(within(ref).getByText('5 candidates · 1 comparable price')).toBeInTheDocument()
    expect(within(ref).getByRole('row', { name: 'Exact match 3' })).toBeInTheDocument()
    expect(within(ref).getByRole('link', { name: /Stompbox page/ })).toHaveAttribute('href', 'https://stompbox.example/SB-001')
  })

  it('shows a difference only for the eligible exact match, never a percentage', async () => {
    const { container } = await open()
    const andertons = rowOf('Boss RC-5 Loop Station | Andertons')
    expect(within(andertons).getByText('−£0.99')).toBeInTheDocument()
    expect(within(andertons).getByText('competitor lower')).toBeInTheDocument()
    expect(within(andertons).getByText('£189.00')).toBeInTheDocument()

    const gear = screen.getByText('gear4music.com').closest('tr')!
    expect(within(gear).getByText('Not compared')).toBeInTheDocument()
    expect(within(gear).getByText('Price status is UNSUPPORTED')).toBeInTheDocument()
    expect(screen.getAllByText('Not compared')).toHaveLength(4)
    expect(container.querySelector('main')!.textContent).not.toMatch(/%|saving|discount/i)
  })

  it('labels non-benchmark competitors and opens evidence with sources and quotes', async () => {
    const user = userEvent.setup()
    await open()
    const gibson = screen.getByText('gibson.com').closest('tr')!
    expect(within(gibson).getByText('Not a price benchmark')).toBeInTheDocument()
    expect(within(gibson).getByText('Review required')).toBeInTheDocument()
    await user.click(within(gibson).getByRole('button', { name: 'Evidence for gibson.com' }))
    // Review rows open on the identity tab: what must be resolved.
    let dialog = screen.getByRole('dialog', { name: 'Gibson' })
    expect(within(dialog).getByRole('tab', { name: 'Identity' })).toHaveAttribute('aria-selected', 'true')
    expect(within(dialog).getByText('Page was not retrieved; identity rests on the search result only')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    const andertons = rowOf('Boss RC-5 Loop Station | Andertons')
    const btn = within(andertons).getByRole('button', { name: 'Evidence for andertons.co.uk' })
    await user.click(btn)
    dialog = screen.getByRole('dialog', { name: 'Andertons' })
    expect(within(dialog).getByRole('tab', { name: 'Price' })).toHaveAttribute('aria-selected', 'true')
    expect(within(dialog).getByText('Was £219.00 Now £189.00')).toBeInTheDocument()
    expect(screen.getByText(/calculated by the backend/).textContent).toBe(
      'Competitor £189.00 − Stompbox £189.99 = −£0.99 (calculated by the backend).',
    )
    expect(within(dialog).getByRole('link', { name: 'https://www.andertons.co.uk/boss-rc-5-loop-station' })).toHaveAttribute(
      'href',
      'https://www.andertons.co.uk/boss-rc-5-loop-station',
    )

    await user.click(within(dialog).getByRole('tab', { name: 'Identity' }))
    expect(within(dialog).getByText('RC-5', { selector: 'dd' })).toBeInTheDocument()
    await user.keyboard('{ArrowRight}')
    expect(within(dialog).getByRole('tab', { name: 'Source' })).toHaveAttribute('aria-selected', 'true')
    expect(within(dialog).getByText(/Retrieval time is not the retailer's last update time/)).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(btn).toHaveFocus() // focus returns to the opener
  })

  it('traps keyboard focus inside the evidence drawer', async () => {
    const user = userEvent.setup()
    await open()
    await user.click(within(rowOf('Boss RC-5 Loop Station | Andertons')).getByRole('button', { name: /Evidence/ }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('button', { name: 'Close' })).toHaveFocus()
    for (let i = 0; i < 12; i++) {
      await user.tab()
      expect(dialog.contains(document.activeElement)).toBe(true)
    }
  })

  it('never shows a difference the backend did not mark eligible', async () => {
    const result = fixture().result
    const gear = result.products[0].matching!.candidates.find((c) => c.competitor.domain === 'gear4music.com')!
    gear.comparison = { ...gear.comparison, eligible: false, difference_gbp: '5.00', competitor_price: '194.99' }
    await open(result)
    const row = screen.getByText('gear4music.com').closest('tr')!
    expect(within(row).getByText('Not compared')).toBeInTheDocument()
    expect(row.textContent).not.toContain('£5.00')
  })

  it('collapses no-match candidates', async () => {
    const result = fixture().result
    const m = result.products[0].matching!
    const noMatch: CandidateMatch = { ...m.candidates[0], candidate_id: 'x', match_status: 'NO_MATCH' }
    m.candidates.push(noMatch)
    const { container } = await open(result)
    const group = container.querySelector('details.group-no_match')!
    expect(group).not.toHaveAttribute('open')
    expect(group.querySelector('summary')).toHaveTextContent(/No match\s*1/)
  })

  it('shows a missing Stompbox price as missing, never zero', async () => {
    const result = fixture().result
    result.products[0].matching!.stompbox_price = null
    await open(result)
    expect(screen.getByText('missing (nothing can be compared)')).toBeInTheDocument()
    expect(screen.getAllByText('missing').length).toBeGreaterThan(0)
    expect(screen.queryByText('£0.00')).not.toBeInTheDocument()
  })

  it('explains empty results and runs without LLM 2', async () => {
    const result = fixture().result
    result.intelligence_mode = 'LLM_DISABLED'
    result.llm2_disabled_reason = 'LLM2_MODEL not set'
    result.products[0].matching!.candidates = []
    await open(result)
    expect(screen.getByText(/Extraction is disabled \(LLM2_MODEL not set\)/)).toBeInTheDocument()
    expect(screen.getByText(/does not mean the product is unavailable/)).toBeInTheDocument()
  })
})
