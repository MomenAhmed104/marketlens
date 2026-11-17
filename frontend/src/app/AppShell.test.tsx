import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import type { ExecutionRun } from '../api'
import App from '../App'
import { at, fixture, installEventSource, makeCandidate, mockAppApi, mockApi, type FakeEventSource } from '../test/fakes'

const F = fixture()
const RUN_ID = F.run.run_id

describe('App shell', () => {
  let sources: FakeEventSource[]
  beforeEach(() => {
    sources = installEventSource()
  })

  it('opens the selected run on Overview and navigates with the sidebar', async () => {
    const user = userEvent.setup()
    mockAppApi()
    at(`?run=${RUN_ID}`)
    render(<App />)

    expect(await screen.findByRole('heading', { level: 1, name: 'Overview' })).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Sections' })
    expect(within(nav).getByRole('link', { name: 'Overview' })).toHaveAttribute('aria-current', 'page')

    await user.click(within(nav).getByRole('link', { name: 'Product Intelligence' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Product Intelligence' })).toBeInTheDocument()
    expect(window.location.hash).toBe('#/products')
    expect(window.location.search).toBe(`?run=${RUN_ID}`) // run kept across pages
    expect(within(nav).getByRole('link', { name: 'Product Intelligence' })).toHaveAttribute('aria-current', 'page')
  })

  it('defaults to the most recent run in memory, or to Live Runs when there is none', async () => {
    mockAppApi({ runs: [] })
    const { unmount } = render(<App />)
    expect(await screen.findByRole('heading', { level: 1, name: 'Live Runs' })).toBeInTheDocument()
    unmount()

    mockAppApi()
    at('')
    render(<App />)
    await waitFor(() => expect(window.location.search).toBe(`?run=${RUN_ID}`))
    expect(
      await screen.findByRole('button', {
        name: 'Selected run: Boss RC-5 Loop Station Guitar Effect Pedal, partially completed',
      }),
    ).toBeInTheDocument()
  })

  it('shows the pending review count and the configuration in the sidebar', async () => {
    mockAppApi({ candidates: [makeCandidate('a.example'), makeCandidate('b.example'), makeCandidate('c.example', 'APPROVED')] })
    at(`?run=${RUN_ID}`, '#/overview')
    render(<App />)
    expect(await screen.findByRole('link', { name: 'Domain Review, 2 pending review' })).toBeInTheDocument()
    const config = screen.getByLabelText('Configuration')
    expect(within(config).getByText('exa')).toBeInTheDocument()
    expect(within(config).getAllByText('off')).toHaveLength(2)
  })

  it('opens and closes the navigation drawer with the keyboard', async () => {
    const user = userEvent.setup()
    mockAppApi()
    at(`?run=${RUN_ID}`)
    render(<App />)
    const menu = await screen.findByRole('button', { name: 'Open navigation' })
    await user.click(menu)
    expect(menu).toHaveAttribute('aria-expanded', 'true')
    expect(document.getElementById('sidebar')).toHaveClass('open')
    await user.keyboard('{Escape}')
    expect(menu).toHaveAttribute('aria-expanded', 'false')
    expect(menu).toHaveFocus()
  })

  it('has a skip link that moves focus to the content without changing the route', async () => {
    const user = userEvent.setup()
    mockAppApi()
    at(`?run=${RUN_ID}`, '#/overview')
    render(<App />)
    await screen.findByRole('heading', { level: 1, name: 'Overview' })
    await user.click(screen.getByRole('link', { name: 'Skip to content' }))
    expect(screen.getByRole('main')).toHaveFocus()
    expect(window.location.hash).toBe('#/overview')
  })

  it('keeps one live stream across page changes and closes it on the terminal event', async () => {
    const user = userEvent.setup()
    let run: ExecutionRun = { ...F.run, status: 'RUNNING', finished_at: null }
    mockAppApi({ runs: [], override: (m, p) => (m === 'GET' && p === `/api/runs/${RUN_ID}` ? { body: run } : undefined) })
    at('', '#/runs')
    render(<App />)

    await user.click(await screen.findByLabelText('Select SB-001'))
    await user.click(screen.getByRole('button', { name: 'Start run (1 product)' }))
    await waitFor(() => expect(sources).toHaveLength(1))

    const nav = screen.getByRole('navigation', { name: 'Sections' })
    expect(await within(nav).findByRole('link', { name: 'Live Runs, run in progress' })).toBeInTheDocument()
    await user.click(within(nav).getByRole('link', { name: 'Overview' }))
    await user.click(within(nav).getByRole('link', { name: 'Domain Review' }))
    expect(sources).toHaveLength(1)
    expect(sources[0].closed).toBe(false)

    run = F.run
    act(() => {
      sources[0].open()
      for (const e of F.events) sources[0].emit(e)
    })
    expect(sources[0].closed).toBe(true)
    // Finished: the status is shown once (in the switcher); no redundant connection badge.
    const banner = screen.getByRole('banner')
    await waitFor(() => expect(within(banner).getByText('Partially completed')).toBeInTheDocument())
    expect(within(banner).queryByText('Live')).not.toBeInTheDocument()
    expect(within(banner).queryByText('Finished')).not.toBeInTheDocument()
  })

  it('switches runs from the keyboard with the run switcher', async () => {
    const user = userEvent.setup()
    const other = { ...F.run, run_id: 'run-other', request: { ...F.run.request, product_ids: ['SB-002'] } }
    mockAppApi({
      runs: [F.run, other],
      override: (_m, p) => (p === '/api/runs/run-other' ? { body: other } : undefined),
    })
    at(`?run=${RUN_ID}`, '#/overview')
    render(<App />)
    const button = await screen.findByRole('button', { name: /^Selected run: Boss RC-5/ })
    await user.click(button)
    const list = await screen.findByRole('listbox', { name: 'Runs in memory' })
    expect(list).toHaveFocus()
    expect(within(list).getAllByRole('option')).toHaveLength(3) // two runs + "no run"
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(button).toHaveFocus()

    await user.click(button)
    await user.keyboard('{ArrowDown}{Enter}')
    await waitFor(() => expect(window.location.search).toBe('?run=run-other'))
    expect(await screen.findByRole('button', { name: /^Selected run: Product SB-002/ })).toBeInTheDocument()
  })

  it('shows the connection badge only while it adds information', async () => {
    mockAppApi({ run: { ...F.run, status: 'RUNNING', finished_at: null } })
    at(`?run=${RUN_ID}`, '#/runs')
    render(<App />)
    await waitFor(() => expect(sources).toHaveLength(1))
    const banner = screen.getByRole('banner')
    expect(within(banner).getByText('Connecting…')).toBeInTheDocument()
    act(() => sources[0].open())
    expect(within(banner).getByText('Live')).toBeInTheDocument()
    act(() => sources[0].fail())
    expect(within(banner).getByText('Reconnecting…')).toBeInTheDocument()
  })

  it('explains when the backend is unavailable', async () => {
    mockApi(() => ({ status: 503, body: { detail: 'down' } }))
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Backend unavailable: down')
  })
})
