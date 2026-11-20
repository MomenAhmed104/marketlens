import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { UNCONFIRMED_BASELINES, baselineWarning } from '../priceBaseline'
import BaselineWarning from './BaselineWarning'

describe('Unconfirmed price baseline', () => {
  it('labels SB-028 and nothing else', () => {
    render(<BaselineWarning productId="SB-028" />)
    const note = screen.getByRole('note')
    expect(note).toHaveTextContent('Unconfirmed baseline')
    expect(note).toHaveTextContent('£579')
    expect(note).toHaveTextContent('provisional')
    expect(Object.keys(UNCONFIRMED_BASELINES)).toEqual(['SB-028'])
    expect(baselineWarning('SB-001')).toBeNull()
    expect(baselineWarning(null)).toBeNull()
  })

  it('renders nothing for a product with a confirmed baseline', () => {
    const { container } = render(<BaselineWarning productId="SB-001" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('never presents £599 as the current price', () => {
    // "was £599" is quoted only as the recorded original price.
    expect(UNCONFIRMED_BASELINES['SB-028']).toMatch(/"was £599"/)
    expect(UNCONFIRMED_BASELINES['SB-028']).not.toMatch(/current price (is )?£599/i)
  })
})
