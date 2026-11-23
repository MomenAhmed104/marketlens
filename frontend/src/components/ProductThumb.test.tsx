import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ProductImage } from '../api'
import { proxied } from '../productImages'
import ProductThumb from './ProductThumb'

const found = (over: Partial<ProductImage> = {}): ProductImage => ({
  status: 'FOUND',
  url: 'https://cdn.shop.example/rc5.jpg',
  source_page_url: 'https://shop.example/rc5',
  source: 'JSON_LD',
  additional_urls: [],
  retrieved_at: '2026-11-22T10:00:00Z',
  note: null,
  ...over,
})

describe('ProductThumb', () => {
  it('loads a found image through the backend proxy, never from the retailer directly', () => {
    render(<ProductThumb image={found()} alt="Boss RC-5" />)
    expect(screen.getByRole('img', { name: 'Boss RC-5' })).toHaveAttribute(
      'src', '/api/images?url=https%3A%2F%2Fcdn.shop.example%2Frc5.jpg')
    expect(proxied('https://a.example/x y.png')).toBe('/api/images?url=https%3A%2F%2Fa.example%2Fx%20y.png')
  })

  it.each([
    [null, 'No image has been collected yet.'],
    [found({ status: 'NOT_FOUND', url: null }), 'No product image was found on the page.'],
    [found({ status: 'INVALID' }), 'The image on the page could not be loaded.'], // a URL alone is not enough
    [found({ status: 'BLOCKED', url: null }), 'The site did not allow its page to be read for an image.'],
    [found({ status: 'NOT_ATTEMPTED', url: null }), 'No image lookup was made for this listing.'],
  ])('shows the same placeholder when there is no usable image (%#)', (image, why) => {
    const { container } = render(<ProductThumb image={image} alt="Item" size="md" />)
    expect(screen.getByRole('img', { name: `No image: ${why}` })).toHaveClass('thumb', 'thumb-md', 'thumb-empty')
    expect(container.querySelector('img')).toBeNull()
  })

  it('falls back to the placeholder when the image fails to load', () => {
    render(<ProductThumb image={found()} alt="Item" />)
    fireEvent.error(screen.getByRole('img', { name: 'Item' }))
    expect(screen.getByRole('img', { name: 'No image: The image could not be loaded.' })).toBeInTheDocument()
  })

  it('opens a larger view with its sources, separate from the product page link', async () => {
    const user = userEvent.setup()
    const outer = vi.fn()
    window.addEventListener('keydown', outer) // e.g. an evidence drawer listening underneath
    render(<ProductThumb image={found()} alt="Boss RC-5" />)
    await user.click(screen.getByRole('button', { name: 'Enlarge image: Boss RC-5' }))
    const dialog = screen.getByRole('dialog', { name: 'Image: Boss RC-5' })
    expect(dialog).toContainElement(screen.getByRole('link', { name: /Product page/ }))
    expect(screen.getByRole('link', { name: /Product page/ })).toHaveAttribute('href', 'https://shop.example/rc5')
    expect(screen.getByRole('link', { name: /Image source/ })).toHaveAttribute('href', 'https://cdn.shop.example/rc5.jpg')
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(outer).not.toHaveBeenCalled() // Escape closed only the viewer
    expect(screen.getByRole('button', { name: 'Enlarge image: Boss RC-5' })).toHaveFocus()
    window.removeEventListener('keydown', outer)
  })

  it('can be shown without a zoom button', () => {
    render(<ProductThumb image={found()} alt="Item" zoomable={false} />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
