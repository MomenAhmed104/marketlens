// Product images in the dashboard: the catalogue image and each competitor listing's own image
// are independent, every place shows an image or the same placeholder, and the image never
// replaces the link to the product page.
import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import type { PipelineResult, ProductImage } from '../api'
import App from '../App'
import { at, fixture, installEventSource, mockAppApi, PRODUCTS } from '../test/fakes'

const RUN_ID = fixture().run.run_id
const img = (url: string): ProductImage => ({
  status: 'FOUND', url, source_page_url: url.replace('/img/', '/p/'), source: 'OPEN_GRAPH', additional_urls: [],
  retrieved_at: '2026-11-22T10:00:00Z', note: null,
})
const CATALOGUE_IMG = 'https://stompbox.example/img/rc5.jpg'

function withListingImages(result: PipelineResult): { result: PipelineResult; urls: Map<string, string> } {
  const urls = new Map<string, string>()
  const products = result.products.map((p) => ({
    ...p,
    intelligence: p.intelligence && {
      ...p.intelligence,
      candidates: p.intelligence.candidates.map((c, i) => {
        if (i % 2 === 1) return { ...c, image: { ...img('x'), status: 'BLOCKED' as const, url: null } }
        const url = `https://${new URL(c.product_url).hostname}/img/listing-${i}.jpg`
        urls.set(c.candidate_id, url)
        return { ...c, image: img(url) }
      }),
    },
  }))
  return { result: { ...result, products }, urls }
}

describe('product images in the dashboard', () => {
  beforeEach(() => {
    installEventSource()
  })

  it('shows the catalogue image in the product header and each listing its own image or a placeholder', async () => {
    const { result, urls } = withListingImages(fixture().result)
    const products = PRODUCTS.map((p) => (p.product_id === 'SB-001' ? { ...p, image: img(CATALOGUE_IMG) } : p))
    mockAppApi({ result, products })
    at(`?run=${RUN_ID}`, '#/products/SB-001')
    render(<App />)
    const hero = (await screen.findByRole('heading', { name: 'Boss RC-5 Loop Station Guitar Effect Pedal' })).closest('section')!
    expect(within(hero).getByRole('img', { name: 'Boss RC-5 Loop Station Guitar Effect Pedal' }).getAttribute('src'))
      .toContain(encodeURIComponent(CATALOGUE_IMG))

    const srcs = [...document.querySelectorAll('table.compare img')].map((i) => decodeURIComponent(i.getAttribute('src')!))
    expect(srcs.length).toBe(urls.size)
    for (const u of urls.values()) expect(srcs.some((s) => s.endsWith(u))).toBe(true)
    expect(srcs.some((s) => s.endsWith(CATALOGUE_IMG))).toBe(false) // never the catalogue image
    expect(document.querySelectorAll('table.compare .thumb-empty').length).toBeGreaterThan(0) // blocked listings

    // Each row keeps its link to the product page next to the image.
    const row = document.querySelector('table.compare tbody tr')!
    expect(within(row as HTMLElement).getAllByRole('link')[0]).toHaveAttribute('href', expect.stringMatching(/^https:/))
  })

  it('shows thumbnails or placeholders in the catalogue listing', async () => {
    const products = PRODUCTS.map((p) => (p.product_id === 'SB-001' ? { ...p, image: img(CATALOGUE_IMG) } : p))
    mockAppApi({ products })
    at(`?run=${RUN_ID}`, '#/runs')
    render(<App />)
    const row1 = (await screen.findByRole('checkbox', { name: 'Select SB-001' })).closest('tr')!
    const row2 = screen.getByRole('checkbox', { name: 'Select SB-002' }).closest('tr')!
    expect(within(row1).getByRole('button', { name: /Enlarge image/ })).toBeInTheDocument()
    expect(within(row2).getByRole('img', { name: 'No image: No image has been collected yet.' })).toBeInTheDocument()
  })
})
