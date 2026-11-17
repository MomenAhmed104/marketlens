import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { makeProduct } from '../test/fakes'
import CataloguePicker from './CataloguePicker'

const PRODUCTS = [
  makeProduct('SB-001', { product_name: 'Boss RC-5 Loop Station', manufacturer_model: 'RC-5' }),
  makeProduct('SB-002', { product_name: 'Boss FZ-5 Fuzz', manufacturer_model: 'FZ-5' }),
  makeProduct('SB-003', { product_name: 'Fender Player Strat', brand: 'Fender', category: 'Guitars', subcategory: 'Electric' }),
  makeProduct('SB-004', { displayed_price: null }),
  makeProduct('SB-005'),
  makeProduct('SB-006'),
  makeProduct('SB-007'),
]

function Harness({ initial = [] as string[] }) {
  const [selected, setSelected] = useState(initial)
  return <CataloguePicker products={PRODUCTS} error={null} selected={selected} onChange={setSelected} />
}

describe('CataloguePicker', () => {
  it('filters by text and by category', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    expect(screen.getByText('7 of 7 products')).toBeInTheDocument()
    await user.type(screen.getByLabelText('Search catalogue'), 'fz-5')
    expect(screen.getByText('1 of 7 products')).toBeInTheDocument()
    expect(screen.getByText('Boss FZ-5 Fuzz')).toBeInTheDocument()

    await user.clear(screen.getByLabelText('Search catalogue'))
    await user.selectOptions(screen.getByLabelText('Category'), 'Guitars')
    expect(screen.getByText('1 of 7 products')).toBeInTheDocument()
    expect(screen.getByText('Fender Player Strat')).toBeInTheDocument()

    await user.type(screen.getByLabelText('Search catalogue'), 'boss')
    expect(screen.getByText('No products match this search.')).toBeInTheDocument()
  })

  it('allows at most 5 products and re-enables after removing one', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    for (const id of ['SB-001', 'SB-002', 'SB-003', 'SB-004', 'SB-005']) {
      await user.click(screen.getByLabelText(`Select ${id}`))
    }
    expect(screen.getByText('5 / 5 selected')).toBeInTheDocument()
    expect(screen.getByText(/maximum reached/)).toBeInTheDocument()
    expect(screen.getByLabelText('Select SB-006')).toBeDisabled()
    expect(screen.getByLabelText('Select SB-001')).toBeEnabled() // selected ones can still be unticked

    await user.click(screen.getByRole('button', { name: 'Remove SB-002' }))
    expect(screen.getByText('4 / 5 selected')).toBeInTheDocument()
    expect(screen.getByLabelText('Select SB-006')).toBeEnabled()
    const chips = screen.getByRole('list', { name: 'Selected products' })
    expect(within(chips).queryByText(/SB-002/)).not.toBeInTheDocument()
  })

  it('shows a missing Stompbox price as missing, never as zero', () => {
    render(<Harness />)
    const row = screen.getByLabelText('Select SB-004').closest('tr')!
    expect(within(row).getByText('price missing')).toBeInTheDocument()
    expect(row.textContent).not.toContain('£0')
    const priced = screen.getByLabelText('Select SB-001').closest('tr')!
    expect(within(priced).getByText('£100.00')).toBeInTheDocument()
  })

  it('shows loading and error states', () => {
    const { rerender } = render(<CataloguePicker products={null} error={null} selected={[]} onChange={() => {}} />)
    expect(screen.getByText('Loading catalogue…')).toBeInTheDocument()
    rerender(<CataloguePicker products={null} error="HTTP 500" selected={[]} onChange={() => {}} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Catalogue unavailable: HTTP 500')
  })
})
