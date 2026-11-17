import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { useReviewer } from './reviewer'

function NameField({ label }: { label: string }) {
  const [reviewer, setReviewer] = useReviewer()
  return <input aria-label={label} value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
}

describe('useReviewer', () => {
  it('shares one reviewer name between mounted components and remembers it', async () => {
    const user = userEvent.setup()
    render(
      <>
        <NameField label="Dashboard reviewer" />
        <NameField label="Review tab reviewer" />
      </>,
    )
    await user.type(screen.getByLabelText('Dashboard reviewer'), 'Sam')
    expect(screen.getByLabelText('Review tab reviewer')).toHaveValue('Sam')
    expect(localStorage.getItem('stompbox.reviewer')).toBe('Sam')
  })
})
