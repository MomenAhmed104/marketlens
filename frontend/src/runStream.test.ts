import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import type { ExecutionEvent, ExecutionRun, PipelineResult } from './api'
import { EVENT_TYPES, mergeEvents, useRunStream } from './runStream'
import { fixture, installEventSource, mockApi, type Call, type FakeEventSource } from './test/fakes'

const F = fixture()
const RUN_ID = F.run.run_id
const ev = (seq: number): ExecutionEvent => F.events[seq - 1]
const terminalSeq = F.events.length // run_completed

describe('EVENT_TYPES', () => {
  it('subscribes to all 19 event types the pipeline emits', () => {
    expect(new Set(EVENT_TYPES).size).toBe(19)
  })
})

describe('mergeEvents', () => {
  it('ignores duplicates, sorts by seq and tracks the highest contiguous seq', () => {
    let s = mergeEvents([], [ev(2), ev(1)])
    expect(s.events.map((e) => e.seq)).toEqual([1, 2])
    expect(s.lastSeq).toBe(2)
    s = mergeEvents(s.events, [ev(2), ev(4)])
    expect(s.events.map((e) => e.seq)).toEqual([1, 2, 4])
    expect(s.lastSeq).toBe(2) // 3 is missing
    s = mergeEvents(s.events, [ev(3)])
    expect(s.lastSeq).toBe(4)
  })
})

describe('useRunStream', () => {
  let sources: FakeEventSource[]
  let run: ExecutionRun
  let result: PipelineResult
  let calls: Call[]
  let runStatus: number

  const routes = (extra: (m: string, p: string) => { status?: number; body: unknown } | undefined = () => undefined) =>
    mockApi((m, p) => {
      const r = extra(m, p)
      if (r) return r
      if (p === `/api/runs/${RUN_ID}`) return { status: runStatus, body: runStatus === 200 ? run : { detail: 'gone' } }
      if (p === `/api/runs/${RUN_ID}/result`) return { body: result }
      const after = p.match(/\/events\?after=(\d+)$/)
      if (after) return { body: F.events.filter((e) => e.seq > Number(after[1])) }
      return undefined
    })

  beforeEach(() => {
    sources = installEventSource()
    const f = fixture()
    run = { ...f.run, status: 'RUNNING', finished_at: null }
    result = f.result
    runStatus = 200
  })

  it('subscribes to named events, streams them and closes on the terminal event', async () => {
    calls = routes()
    const { result: hook } = renderHook(() => useRunStream(RUN_ID))
    await waitFor(() => expect(sources).toHaveLength(1))
    const es = sources[0]
    expect(es.url).toBe(`/api/runs/${RUN_ID}/stream`)
    for (const t of EVENT_TYPES) expect(es.listensTo(t)).toBe(true)

    act(() => es.open())
    expect(hook.current.connection).toBe('live')
    act(() => {
      for (let s = 1; s <= 3; s++) es.emit(ev(s))
      es.emit(ev(2)) // duplicate (e.g. replay after reconnect)
    })
    expect(hook.current.events.map((e) => e.seq)).toEqual([1, 2, 3])
    expect(hook.current.lastSeq).toBe(3)

    run = fixture().run // now PARTIALLY_COMPLETED
    act(() => {
      for (let s = 4; s <= terminalSeq; s++) es.emit(ev(s))
    })
    expect(es.closed).toBe(true) // otherwise EventSource would reconnect forever
    expect(hook.current.connection).toBe('closed')
    await waitFor(() => expect(hook.current.run?.status).toBe('PARTIALLY_COMPLETED'))
    expect(hook.current.result?.products[0].product_id).toBe('SB-001')
    expect(calls.some((c) => c.path.includes('/events'))).toBe(false)
  })

  it('fills a sequence gap from the events endpoint', async () => {
    calls = routes()
    const { result: hook } = renderHook(() => useRunStream(RUN_ID))
    await waitFor(() => expect(sources).toHaveLength(1))
    act(() => {
      sources[0].emit(ev(1))
      sources[0].emit(ev(3))
    })
    await waitFor(() => expect(hook.current.lastSeq).toBeGreaterThanOrEqual(3))
    expect(calls.some((c) => c.path === `/api/runs/${RUN_ID}/events?after=1`)).toBe(true)
  })

  it('reports an expired run (404) without opening a stream', async () => {
    runStatus = 404
    routes()
    const { result: hook } = renderHook(() => useRunStream(RUN_ID))
    await waitFor(() => expect(hook.current.connection).toBe('expired'))
    expect(sources).toHaveLength(0)
  })

  it('loads a finished run from the events endpoint without a stream', async () => {
    run = fixture().run
    calls = routes()
    const { result: hook } = renderHook(() => useRunStream(RUN_ID))
    await waitFor(() => expect(hook.current.connection).toBe('closed'))
    expect(sources).toHaveLength(0)
    expect(hook.current.events).toHaveLength(terminalSeq)
    expect(calls.some((c) => c.path === `/api/runs/${RUN_ID}/events?after=0`)).toBe(true)
  })

  it('shows reconnecting, then falls back to polling when the stream is refused', async () => {
    run = { ...run }
    calls = routes((_m, p) => (p.includes('/events?') ? { body: F.events } : undefined))
    const { result: hook } = renderHook(() => useRunStream(RUN_ID))
    await waitFor(() => expect(sources).toHaveLength(1))
    act(() => sources[0].fail())
    expect(hook.current.connection).toBe('reconnecting')
    run = fixture().run
    act(() => sources[0].fail(true))
    expect(sources[0].closed).toBe(true)
    await waitFor(() => expect(hook.current.connection).toBe('closed'))
    expect(hook.current.events).toHaveLength(terminalSeq)
  })

  it('stops when the run disappears while streaming', async () => {
    routes()
    const { result: hook } = renderHook(() => useRunStream(RUN_ID))
    await waitFor(() => expect(sources).toHaveLength(1))
    runStatus = 404
    act(() => sources[0].emit(ev(1))) // triggers a refresh, which now gets 404
    await waitFor(() => expect(hook.current.connection).toBe('expired'))
    expect(sources[0].closed).toBe(true)
  })

  it('closes the stream on unmount', async () => {
    routes()
    const { unmount } = renderHook(() => useRunStream(RUN_ID))
    await waitFor(() => expect(sources).toHaveLength(1))
    unmount()
    expect(sources[0].closed).toBe(true)
  })
})
