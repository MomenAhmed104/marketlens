import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { isLandingRoute } from '../app/router'
import { at, fixture, installEventSource, mockAppApi, type Call } from '../test/fakes'

const RUN_ID = fixture().run.run_id

describe('landing page routing', () => {
  it('shows the landing page only for an empty route or #/home', () => {
    expect(isLandingRoute('', '')).toBe(true)
    expect(isLandingRoute('#', '')).toBe(true)
    expect(isLandingRoute('#/', '')).toBe(true)
    expect(isLandingRoute('#/home', '')).toBe(true)
    expect(isLandingRoute('#/home', `?run=${RUN_ID}`)).toBe(true)
    // Every existing dashboard link still opens the dashboard.
    for (const h of ['#/dashboard', '#/overview', '#/products', '#/products/SB-001', '#/runs', '#/domains']) {
      expect(isLandingRoute(h, ''), h).toBe(false)
    }
    expect(isLandingRoute('', `?run=${RUN_ID}`)).toBe(false) // an old ?run= link without a hash
  })
})

describe('landing page', () => {
  let calls: Call[]
  beforeEach(() => {
    installEventSource()
    calls = mockAppApi()
  })

  it('renders without any backend call, with the agreed copy and no invented figures', async () => {
    at('')
    render(<App />)
    expect(await screen.findByRole('heading', { level: 1, name: /Know Your Market\.\s*Price With Confidence\./ })).toBeInTheDocument()
    expect(screen.getByText('Your competitors. Your products. One intelligence platform.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Turn Competitor Data Into Better Pricing Decisions.' })).toBeInTheDocument()
    for (const h of ['Competitor Discovery', 'Product Intelligence', 'Price Intelligence', 'Evidence-Based Insights']) {
      expect(screen.getByRole('heading', { level: 3, name: h })).toBeInTheDocument()
    }
    expect(screen.getAllByRole('listitem').filter((li) => li.closest('.lp-steps'))).toHaveLength(4)
    expect(calls).toHaveLength(0) // the landing page never needs the backend
    expect(screen.getAllByText(/Recorded run, 20 Nov 2026/).length).toBeGreaterThan(0) // screenshots are labelled
  })

  it('has accessible navigation that scrolls to sections without changing the route', async () => {
    const scroll = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scroll })
    const user = userEvent.setup()
    at('')
    render(<App />)
    const nav = await screen.findByRole('navigation', { name: 'Landing page' })
    expect(within(nav).getAllByRole('button').map((b) => b.textContent)).toEqual(['Home', 'Platform', 'How It Works', 'Capabilities'])
    await user.click(within(nav).getByRole('button', { name: 'How It Works' }))
    expect(scroll).toHaveBeenCalled()
    expect(window.location.hash).toBe('') // in-page navigation never touches the dashboard routes
    delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView
  })

  it('switches the product showcase between real dashboard screenshots', async () => {
    const user = userEvent.setup()
    at('')
    render(<App />)
    const tabs = await screen.findByRole('tablist', { name: 'Dashboard views' })
    expect(within(tabs).getAllByRole('tab').map((t) => t.textContent)).toEqual(
      ['Overview', 'Product Intelligence', 'Evidence drawer', 'Domain Review', 'Live Runs'])
    await user.click(within(tabs).getByRole('tab', { name: 'Evidence drawer' }))
    expect(screen.getByRole('img', { name: 'Screenshot of the dashboard: Evidence drawer' })).toHaveAttribute('src', '/landing/evidence.webp')
  })

  it('Explore Dashboard opens the dashboard, and the dashboard links back home', async () => {
    const user = userEvent.setup()
    at('')
    render(<App />)
    const cta = await screen.findByRole('link', { name: 'Explore Dashboard' })
    expect(cta).toHaveAttribute('href', '#/dashboard')
    await user.click(cta)
    await waitFor(() => expect(window.location.hash).toBe('#/dashboard'))
    expect(await screen.findByRole('heading', { level: 1, name: 'Overview' })).toBeInTheDocument()
    await waitFor(() => expect(window.location.search).toBe(`?run=${RUN_ID}`))

    const home = screen.getByRole('link', { name: /Back to home/ })
    expect(home).toHaveAttribute('href', '#/home')
    await user.click(home)
    expect(await screen.findByRole('heading', { level: 1, name: /Know Your Market/ })).toBeInTheDocument()
  })

  it('keeps existing dashboard deep links working', async () => {
    at(`?run=${RUN_ID}`, '#/products/SB-001')
    render(<App />)
    expect(await screen.findByRole('heading', { level: 1, name: 'Product Intelligence' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Know Your Market/ })).not.toBeInTheDocument()
  })
})
