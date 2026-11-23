import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import ScrollRow from './ScrollRow'
import Tabs from './Tabs'

function size(el: HTMLElement, { scrollWidth, clientWidth, scrollLeft = 0 }: Record<string, number>) {
  Object.defineProperty(el, 'scrollWidth', { configurable: true, value: scrollWidth })
  Object.defineProperty(el, 'clientWidth', { configurable: true, value: clientWidth })
  Object.defineProperty(el, 'scrollLeft', { configurable: true, writable: true, value: scrollLeft })
}

describe('ScrollRow', () => {
  it('shows a "more" hint only on the sides where items are hidden', () => {
    const { container } = render(
      <ScrollRow aria-label="Row" role="list">
        <span>a</span>
      </ScrollRow>,
    )
    const row = container.querySelector('.scroll-row')!
    const track = screen.getByRole('list', { name: 'Row' })
    expect(row).not.toHaveClass('more-right')

    size(track, { scrollWidth: 800, clientWidth: 390 })
    act(() => void window.dispatchEvent(new Event('resize')))
    expect(row).toHaveClass('more-right')
    expect(row).not.toHaveClass('more-left')

    size(track, { scrollWidth: 800, clientWidth: 390, scrollLeft: 410 })
    fireEvent.scroll(track)
    expect(row).toHaveClass('more-left')
    expect(row).not.toHaveClass('more-right')
    expect(container.querySelectorAll('.scroll-hint[aria-hidden="true"]')).toHaveLength(2)
  })

  it('keeps tabs a single accessible tablist and scrolls the chosen tab into view', async () => {
    const reveal = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: reveal })
    const user = userEvent.setup()
    const tabs = ['one', 'two', 'three', 'four', 'five'] as const
    function Harness() {
      const [v, setV] = useState<(typeof tabs)[number]>('one')
      return <Tabs label="Sections" idPrefix="t" value={v} onChange={setV} tabs={tabs.map((id) => ({ id, label: id }))} />
    }
    render(<Harness />)
    expect(screen.getByRole('tablist', { name: 'Sections' })).toHaveClass('tabs-line')
    expect(screen.getAllByRole('tab')).toHaveLength(5)
    expect(reveal).not.toHaveBeenCalled() // no scroll on first render
    await user.click(screen.getByRole('tab', { name: 'five' }))
    expect(reveal).toHaveBeenCalledWith({ block: 'nearest', inline: 'nearest' })
    delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView
  })
})
