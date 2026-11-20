import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import DistributionBar from './DistributionBar'

describe('DistributionBar', () => {
  it('draws one segment per non-zero status, sized by count, with a data table', () => {
    const { container } = render(
      <DistributionBar caption="Candidates" counts={{ EQUIVALENT_VARIANT: 0, EXACT_MATCH: 3, REVIEW_REQUIRED: 0, SIMILAR_MATCH: 1, NO_MATCH: 9 }} />,
    )
    const segs = container.querySelectorAll<HTMLElement>('.dist-seg')
    expect([...segs].map((s) => s.style.flexGrow)).toEqual(['3', '1', '9'])
    const table = screen.getByRole('table', { name: 'Candidates' })
    expect(within(table).getByRole('row', { name: 'Review required 0' })).toBeInTheDocument()
    expect(within(table).getByRole('row', { name: 'Total 13' })).toBeInTheDocument()
    expect(container.textContent).not.toContain('%')
  })

  it('renders an empty bar when there are no candidates', () => {
    const { container } = render(
      <DistributionBar caption="None" counts={{ EQUIVALENT_VARIANT: 0, EXACT_MATCH: 0, REVIEW_REQUIRED: 0, SIMILAR_MATCH: 0, NO_MATCH: 0 }} />,
    )
    expect(container.querySelector('.dist-bar')).toHaveClass('dist-empty')
    expect(container.querySelectorAll('.dist-seg')).toHaveLength(0)
  })
})
