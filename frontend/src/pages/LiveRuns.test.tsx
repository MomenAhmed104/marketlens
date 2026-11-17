import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import type { ExecutionRun, StageStatus } from '../api'
import App from '../App'
import { at, fixture, installEventSource, makeCandidate, mockAppApi, type Call, type FakeEventSource } from '../test/fakes'

const F = fixture()
const RUN_ID = F.run.run_id
const running = (): ExecutionRun => ({ ...fixture().run, status: 'RUNNING', finished_at: null })

describe('Live Runs page', () => {
  let sources: FakeEventSource[]
  let calls: Call[]
  beforeEach(() => {
    sources = installEventSource()
  })

  it('starts a run for the selected products and follows it live', async () => {
    const user = userEvent.setup()
    calls = mockAppApi({ run: running(), runs: [] })
    at('', '#/runs')
    render(<App />)
    const start = await screen.findByRole('button', { name: /Start run/ })
    expect(start).toBeDisabled()
    expect(screen.getByText('Select at least one product.')).toBeInTheDocument()

    await user.click(await screen.findByLabelText('Select SB-001'))
    await user.click(screen.getByRole('button', { name: 'Start run (1 product)' }))
    expect(calls.find((c) => c.method === 'POST' && c.path === '/api/runs')?.body).toEqual({
      product_ids: ['SB-001'],
      run_discovery: true,
      domains: null,
    })
    await waitFor(() => expect(sources).toHaveLength(1))
    expect(sources[0].url).toBe(`/api/runs/${RUN_ID}/stream`)
    expect(window.location.search).toBe(`?run=${RUN_ID}`)
    expect(await screen.findByRole('list', { name: 'Pipeline stages' })).toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: 'Pipeline stages' })).getAllByRole('listitem').length).toBeGreaterThanOrEqual(7)
    expect(screen.getByLabelText('Select SB-002')).toBeDisabled() // locked while running

    act(() => {
      sources[0].open()
      sources[0].emit(F.events[0])
    })
    expect(within(screen.getByRole('banner')).getByText('Live')).toBeInTheDocument()
    expect(screen.getByText('Run started for 1 product(s)')).toBeInTheDocument()
  })

  it('shows the backend classification of each domain as soon as its competitor_discovered event arrives', async () => {
    calls = mockAppApi({ run: running() })
    at(`?run=${RUN_ID}`, '#/runs')
    render(<App />)
    await waitFor(() => expect(sources).toHaveLength(1))
    const discovered = F.events.filter((e) => e.type === 'competitor_discovered')
    expect(discovered.every((e) => typeof e.payload.competitor_class === 'string')).toBe(true) // backend payload
    const upTo = Math.max(...discovered.map((e) => e.seq))
    const events = F.events
      .filter((e) => e.seq <= upTo)
      .map((e) => {
        if (e.payload.domain === 'beckettsmusic.co.uk' && e.type === 'competitor_discovered')
          return { ...e, payload: { ...e.payload, competitor_class: 'DIRECT_COMPETITOR' } }
        if (e.payload.domain === 'lookalike-music.co.uk' && e.type === 'competitor_discovered') {
          const { competitor_class: _c, proposed_competitor_class: _p, ...older } = e.payload
          return { ...e, payload: older } // an event from an older backend: no class
        }
        return e
      })
    act(() => {
      sources[0].open()
      events.forEach((e) => sources[0].emit(e))
    })
    const item = (domain: string) =>
      screen.getByText((text) => text.startsWith(`Discovered ${domain} (`)).closest<HTMLElement>('.feed-item')!
    expect(within(item('beckettsmusic.co.uk')).getByTestId('competitor-class')).toHaveTextContent('Direct Competitor')
    expect(within(item('ebay.co.uk')).getByTestId('competitor-class')).toHaveTextContent('Related Retailer')
    expect(within(item('guitarworld.com')).getByTestId('competitor-class')).toHaveTextContent('Needs Review')
    expect(within(item('lookalike-music.co.uk')).queryByTestId('competitor-class')).not.toBeInTheDocument()
  })

  it('shows why a run could not start', async () => {
    const user = userEvent.setup()
    mockAppApi({
      runs: [],
      override: (m, p) =>
        m === 'POST' && p === '/api/runs' ? { status: 429, body: { detail: '2 run(s) already active (limit 2)' } } : undefined,
    })
    at('', '#/runs')
    render(<App />)
    await user.click(await screen.findByLabelText('Select SB-001'))
    await user.click(screen.getByRole('button', { name: /Start run/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not start the run: 2 run(s) already active (limit 2)')
    expect(sources).toHaveLength(0)
  })

  it('cancels only after confirmation, and handles a run that already finished', async () => {
    const user = userEvent.setup()
    let finished = false
    calls = mockAppApi({
      run: running(),
      override: (m, p) =>
        finished && m === 'POST' && p.endsWith('/cancel') ? { status: 409, body: { detail: 'Run already finished' } } : undefined,
    })
    at(`?run=${RUN_ID}`, '#/runs')
    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'Cancel run' }))
    expect(calls.some((c) => c.path.endsWith('/cancel'))).toBe(false) // not yet
    const confirm = screen.getByRole('button', { name: 'Confirm cancel' })
    expect(confirm).toHaveFocus()
    await user.click(screen.getByRole('button', { name: 'Keep running' }))
    expect(calls.some((c) => c.path.endsWith('/cancel'))).toBe(false)
    expect(screen.getByRole('button', { name: 'Cancel run' })).toHaveFocus()

    await user.click(screen.getByRole('button', { name: 'Cancel run' }))
    await user.click(screen.getByRole('button', { name: 'Confirm cancel' }))
    expect(calls.some((c) => c.method === 'POST' && c.path === `/api/runs/${RUN_ID}/cancel`)).toBe(true)

    finished = true
    await user.click(screen.getByRole('button', { name: 'Cancel run' }))
    await user.click(screen.getByRole('button', { name: 'Confirm cancel' }))
    expect(await screen.findByText('The run had already finished.')).toBeInTheDocument()
  })

  it('shows every stage state distinctly', async () => {
    const run = running()
    const states: StageStatus[] = ['COMPLETED', 'COMPLETED', 'SKIPPED', 'PARTIAL', 'RUNNING', 'PENDING', 'FAILED']
    run.stages.forEach((s, i) => (s.status = states[i]))
    run.stages[3].errors = ['1 domain failed']
    mockAppApi({ run })
    at(`?run=${RUN_ID}`, '#/runs')
    render(<App />)
    const stepper = await screen.findByRole('list', { name: 'Pipeline stages' })
    const steps = within(stepper).getAllByRole('listitem').filter((li) => li.classList.contains('step'))
    expect(steps).toHaveLength(7)
    expect(steps.map((s) => s.querySelector('.pill')!.textContent)).toEqual([
      '✓Completed', '✓Completed', '–Skipped', '!Partial', '●Running', '–Pending', '✕Failed',
    ])
    expect(steps[4]).toHaveAttribute('aria-current', 'step')
    expect(within(steps[3]).getByText('1 domain failed')).toBeInTheDocument()
  })

  it('reopens a finished run with its summary, timeline filters and re-run option', async () => {
    const user = userEvent.setup()
    mockAppApi({ candidates: [makeCandidate('newshop.example', 'APPROVED', '2026-11-10T14:00:00Z')] })
    at(`?run=${RUN_ID}`, '#/runs')
    render(<App />)
    expect(await screen.findByText(/Some stages, domains or products failed/)).toBeInTheDocument()
    expect(sources).toHaveLength(0) // finished: no stream
    expect(screen.getByRole('button', { name: 'Re-run these products' })).toBeEnabled()
    expect(await screen.findByText(/A re-run will also search 1 domain approved since this run: newshop.example/)).toBeInTheDocument()

    const items = () => screen.getByRole('list', { name: 'Run events, newest first' }).querySelectorAll('.feed-item')
    expect(items()).toHaveLength(F.events.length)
    await user.click(screen.getByRole('button', { name: 'Matching and prices' }))
    expect(screen.getByRole('button', { name: 'Matching and prices' })).toHaveAttribute('aria-pressed', 'true')
    expect(items()).toHaveLength(F.events.filter((e) => e.type === 'match_validated' || e.type === 'price_validated').length)
    expect(screen.getAllByRole('heading', { level: 3, name: 'Matching and prices' }).length).toBeGreaterThan(0)
    const products = screen.getByRole('heading', { level: 2, name: 'Products' }).closest('section')!
    expect(within(products).getByRole('link', { name: 'View results for SB-001' })).toHaveAttribute('href', '#/products/SB-001')
  })

  it('explains an expired run', async () => {
    const user = userEvent.setup()
    mockAppApi({ run: null, runs: [] })
    at('?run=run-gone', '#/runs')
    render(<App />)
    expect(await screen.findByText(/is no longer available/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Close run' }))
    expect(window.location.search).toBe('')
  })
})
