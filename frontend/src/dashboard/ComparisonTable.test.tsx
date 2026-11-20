import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { CandidateMatch, CompetitorProduct } from '../api'
import { fixture } from '../test/fakes'
import ComparisonTable from './ComparisonTable'

// Backend-generated fixture rows, reshaped into an equivalent variant (as the backend
// would return it) to check the separation of direct and alternative-product prices.
function setup() {
  const { result } = fixture()
  const pr = result.products[0]
  const pages = new Map<string, CompetitorProduct>(pr.intelligence!.candidates.map((c) => [c.candidate_id, c]))
  const exact = pr.matching!.candidates.find((c) => c.price_kind === 'DIRECT')!
  const equivalent: CandidateMatch = {
    ...exact,
    candidate_id: 'SB-001:equivalent',
    product_url: 'https://www.thomann.co.uk/boss-rc-5-black',
    match_status: 'EQUIVALENT_VARIANT',
    comparison: { ...exact.comparison, eligible: false, difference_gbp: null, reasons: ['Match status is EQUIVALENT_VARIANT, not EXACT_MATCH'] },
    price_kind: 'ALTERNATIVE',
    proposed_match: 'EXACT_MATCH',
    proposal_agreement: 'PYTHON_STRICTER',
    alternative_price: {
      shown: true,
      match_status: 'EQUIVALENT_VARIANT',
      reasons: [],
      price: '179.00',
      currency: 'GBP',
      price_text: '£179.00',
      condition: 'UNKNOWN',
      condition_label: 'Condition not stated',
      benchmark_eligible: false,
    },
    intelligence: { ...exact.intelligence!, uncertainties: ['LLM 2 proposed EXACT_MATCH; Python gave EQUIVALENT_VARIANT'] },
  }
  pages.set(equivalent.candidate_id, pages.get(exact.candidate_id)!)
  render(<ComparisonTable candidates={[exact, equivalent]} pages={pages} stompboxPrice="189.99" stompboxCurrency="GBP" />)
  return { exact, equivalent }
}

describe('ComparisonTable', () => {
  it('keeps alternative-product prices in their own section, without a difference', () => {
    setup()
    const direct = screen.getByRole('region', { name: 'Exact matches (1)' })
    expect(within(direct).getByText('−£0.99')).toBeInTheDocument()
    const alt = screen.getByRole('region', { name: 'Alternative products' })
    const row = within(alt).getByRole('region', { name: 'Equivalent variants (1)' })
    expect(within(row).getByText('£179.00')).toBeInTheDocument()
    expect(within(row).getByText('Information only · not benchmarked')).toBeInTheDocument()
    expect(within(alt).queryByText(/Difference|competitor lower|competitor higher/)).not.toBeInTheDocument()
    expect(within(row).getByText(/LLM 2 proposed exact match/)).toHaveTextContent('Python more cautious')
    expect(within(direct).queryByText('£179.00')).not.toBeInTheDocument()
  })

  it('marks which option price matched the Stompbox variant and keeps the others as alternatives', async () => {
    const user = userEvent.setup()
    const { result } = fixture()
    const pr = result.products[0]
    const exact = pr.matching!.candidates.find((c) => c.price_kind === 'DIRECT')!
    const page = { ...pr.intelligence!.candidates.find((c) => c.candidate_id === exact.candidate_id)! }
    const base = { status: 'EXTRACTED', currency: 'GBP', note: null, quote: null }
    page.option_prices = [
      { ...base, option: 'Black', price_text: '£499.00', amount: '499.00', matches_catalogue_variant: true },
      { ...base, option: 'Sunburst', price_text: '£479.00', amount: '479.00', matches_catalogue_variant: false },
    ]
    render(
      <ComparisonTable
        candidates={[exact]}
        pages={new Map([[exact.candidate_id, page]])}
        stompboxPrice="189.99"
        stompboxCurrency="GBP"
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Evidence for andertons.co.uk' }))
    const dialog = screen.getByRole('dialog')
    const black = within(dialog).getByText(/Black: £499.00/).closest('li')!
    expect(within(black).getByText('matches the Stompbox variant')).toBeInTheDocument()
    const sunburst = within(dialog).getByText(/Sunburst: £479.00/).closest('li')!
    expect(within(sunburst).getByText('Alternative option, not the Stompbox variant')).toBeInTheDocument()
  })

  it('lists ignored prices with their backend category, keeping delivery charges apart from sales', async () => {
    const user = userEvent.setup()
    const { result } = fixture()
    const pr = result.products[0]
    const exact = pr.matching!.candidates.find((c) => c.price_kind === 'DIRECT')!
    const page = { ...pr.intelligence!.candidates.find((c) => c.candidate_id === exact.candidate_id)! }
    page.ignored_price_details = [
      { text: 'Was £219.00', category: 'SALE' },
      { text: 'Next day delivery £4.99', category: 'DELIVERY' },
      { text: '£15.75 per month', category: 'FINANCE' },
    ]
    page.specifications = [{ name: 'Power', value: '9V AC Adapter/Battery', key: 'power_supply', category: 'power_supply' }]
    render(
      <ComparisonTable
        candidates={[exact]}
        pages={new Map([[exact.candidate_id, page]])}
        stompboxPrice="189.99"
        stompboxCurrency="GBP"
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Evidence for andertons.co.uk' }))
    const dialog = screen.getByRole('dialog')
    expect(within(within(dialog).getByText('Next day delivery £4.99').closest('li')!).getByText('Delivery charge')).toBeInTheDocument()
    expect(within(within(dialog).getByText('Was £219.00').closest('li')!).getByText('Sale / was price')).toBeInTheDocument()
    expect(within(dialog).getByText('Finance')).toBeInTheDocument()
  })

  it('shows the LLM 2 proposal next to Python’s status and the uncertainties in the evidence drawer', async () => {
    const user = userEvent.setup()
    setup()
    await user.click(screen.getAllByRole('button', { name: 'Evidence for andertons.co.uk' })[1])
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Alternative-product price', { selector: 'strong' })).toBeInTheDocument()
    expect(within(dialog).getByText(/never a price benchmark and no difference is calculated/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('tab', { name: 'Identity' }))
    expect(within(dialog).getByText('LLM 2 match proposal')).toBeInTheDocument()
    expect(within(dialog).getByText('LLM 2 proposed EXACT_MATCH; Python gave EQUIVALENT_VARIANT')).toBeInTheDocument()
    expect(within(dialog).getByText('Same model and product')).toBeInTheDocument() // verified Task A reason
  })
})
