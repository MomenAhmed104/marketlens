import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import type { PipelineResult } from '../api'
import App from '../App'
import { at, fixture, installEventSource, mockAppApi } from '../test/fakes'

const RUN_ID = fixture().run.run_id

/** The generated fixture only has SB-001: relabel it as SB-028 (display test only; no prices change). */
function asSb028(): PipelineResult {
  const r = structuredClone(fixture().result) as PipelineResult
  for (const p of r.products) {
    p.product_id = 'SB-028'
    if (p.matching) {
      p.matching.product_id = 'SB-028'
      for (const c of p.matching.candidates) c.stompbox_product_id = 'SB-028'
    }
  }
  return r
}

const notes = () => screen.queryAllByTestId('baseline-warning')

describe('SB-028 unconfirmed price baseline on the pages', () => {
  beforeEach(() => {
    installEventSource()
  })

  it('shows the warning in the product header and the evidence drawer for SB-028', async () => {
    mockAppApi({ result: asSb028() })
    at(`?run=${RUN_ID}`, '#/products/SB-028')
    render(<App />)
    await screen.findByRole('heading', { name: 'Competitor comparison' })
    const hero = document.querySelector('.product-price') as HTMLElement
    expect(within(hero).getByTestId('baseline-warning')).toHaveTextContent('Unconfirmed baseline')

    const user = userEvent.setup()
    const row = screen.getByRole('link', { name: 'Boss RC-5 Loop Station | Andertons' }).closest('tr')!
    await user.click(within(row).getByRole('button', { name: 'Evidence for andertons.co.uk' }))
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('tab', { name: 'Price' }))
    expect(within(dialog).getByTestId('baseline-warning')).toHaveTextContent('provisional')
    // Display only: the backend difference is shown unchanged next to the warning.
    expect(within(dialog).getByText('−£0.99')).toBeInTheDocument()
  })

  it('shows the warning on the Overview price card for SB-028', async () => {
    mockAppApi({ result: asSb028() })
    at(`?run=${RUN_ID}`, '#/overview')
    render(<App />)
    await screen.findByRole('heading', { name: /exact match/ })
    const cards = [...document.querySelectorAll('li.price-card')]
    expect(cards.length).toBeGreaterThan(0)
    for (const card of cards) expect(within(card as HTMLElement).getByTestId('baseline-warning')).toBeInTheDocument()
  })

  it('shows no warning for SB-001', async () => {
    mockAppApi({})
    at(`?run=${RUN_ID}`, '#/products/SB-001')
    render(<App />)
    await screen.findByRole('heading', { name: 'Competitor comparison' })
    expect(notes()).toHaveLength(0)
  })
})

describe('Comparison table Evidence action', () => {
  beforeEach(() => {
    installEventSource()
  })

  it('keeps every Evidence button in the pinned action column', async () => {
    mockAppApi({})
    at(`?run=${RUN_ID}`, '#/products/SB-001')
    render(<App />)
    await screen.findByRole('heading', { name: 'Competitor comparison' })
    const buttons = screen.getAllByRole('button', { name: /^Evidence for / })
    expect(buttons.length).toBeGreaterThan(0)
    for (const b of buttons) expect(b.closest('td')).toHaveClass('col-action')
    for (const table of document.querySelectorAll('table.compare')) {
      expect(table.querySelector('thead th.col-action')).not.toBeNull()
      expect(table.closest('.table-scroll')).not.toBeNull() // narrow screens keep horizontal scrolling
    }
  })
})
