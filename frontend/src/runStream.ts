// Live run state for the dashboard: SSE stream + run/result refreshes.
//
// - Events arrive over SSE (`GET /api/runs/{id}/stream`) with named `event:` types,
//   so a listener is registered per known type; `onmessage` would never fire.
// - Sequence numbers are contiguous per run. Duplicates are ignored, and a gap
//   (for example an event type this client does not know) is filled from
//   `GET /api/runs/{id}/events?after=`.
// - The browser reconnects with Last-Event-ID by itself. The stream is closed
//   explicitly on the terminal event, otherwise EventSource would reconnect forever.
// - If the stream keeps failing, the hook falls back to polling `/events`.
// - Large data (stages, results) comes from `/api/runs/{id}` and `/result`,
//   refetched at most once per REFRESH_MS while events arrive.
import { useEffect, useReducer, useRef } from 'react'
import {
  ApiError,
  fetchRun,
  fetchRunEvents,
  fetchRunResult,
  runStreamUrl,
  TERMINAL_RUN_STATUSES,
  type ExecutionEvent,
  type ExecutionRun,
  type PipelineResult,
} from './api'

export const EVENT_TYPES = [
  'run_started',
  'run_completed',
  'run_failed',
  'run_cancelled',
  'stage_started',
  'stage_completed',
  'product_loaded',
  'competitor_discovered',
  'domain_auto_approved',
  'domain_approval_required',
  'known_domains_loaded',
  'search_started',
  'page_retrieved',
  'extraction_skipped',
  'competitor_product_extracted',
  'match_validated',
  'price_validated',
  'product_completed',
  'product_failed',
] as const

export const TERMINAL_EVENT_TYPES = ['run_completed', 'run_failed', 'run_cancelled']

export const REFRESH_MS = 1000
export const POLL_MS = 2000
export const MAX_SSE_ERRORS = 3
const ES_CLOSED = 2 // EventSource.CLOSED

export type Connection =
  | 'idle'
  | 'connecting'
  | 'live'
  | 'reconnecting'
  | 'polling'
  | 'closed' // run finished; stream closed
  | 'expired' // 404: pruned, or the backend restarted
  | 'error'

export interface RunView {
  runId: string | null
  run: ExecutionRun | null
  result: PipelineResult | null
  events: ExecutionEvent[] // ordered by seq, no duplicates
  lastSeq: number // highest contiguous seq received
  connection: Connection
  error: string | null
}

type Action =
  | { type: 'reset'; runId: string | null }
  | { type: 'run'; run: ExecutionRun }
  | { type: 'result'; result: PipelineResult }
  | { type: 'events'; events: ExecutionEvent[] }
  | { type: 'connection'; connection: Connection }
  | { type: 'error'; error: string | null }

export const initialView = (runId: string | null): RunView => ({
  runId,
  run: null,
  result: null,
  events: [],
  lastSeq: 0,
  connection: runId ? 'connecting' : 'idle',
  error: null,
})

/** Merge events by seq: ignore duplicates, keep order, track the highest contiguous seq. */
export function mergeEvents(existing: ExecutionEvent[], incoming: ExecutionEvent[]) {
  const seen = new Set(existing.map((e) => e.seq))
  const fresh = incoming.filter((e) => {
    if (seen.has(e.seq)) return false
    seen.add(e.seq)
    return true
  })
  const events = fresh.length ? [...existing, ...fresh].sort((a, b) => a.seq - b.seq) : existing
  let lastSeq = 0
  while (seen.has(lastSeq + 1)) lastSeq++
  return { events, lastSeq }
}

export function runViewReducer(state: RunView, action: Action): RunView {
  switch (action.type) {
    case 'reset':
      return initialView(action.runId)
    case 'run':
      return { ...state, run: action.run }
    case 'result':
      return { ...state, result: action.result }
    case 'events':
      return { ...state, ...mergeEvents(state.events, action.events) }
    case 'connection':
      return { ...state, connection: action.connection }
    case 'error':
      return { ...state, error: action.error }
  }
}

export const isTerminal = (run: ExecutionRun | null) => !!run && TERMINAL_RUN_STATUSES.includes(run.status)

export function useRunStream(runId: string | null): RunView & { refresh: () => void } {
  const [view, dispatch] = useReducer(runViewReducer, runId, initialView)
  const refreshRef = useRef<() => void>(() => {})

  useEffect(() => {
    dispatch({ type: 'reset', runId })
    if (!runId) return

    let disposed = false
    let terminal = false
    let es: EventSource | null = null
    let sseErrors = 0
    let pollTimer: ReturnType<typeof setTimeout> | undefined
    let refreshTimer: ReturnType<typeof setTimeout> | undefined
    let refreshPending = false
    let refreshIssued = 0
    let refreshApplied = 0
    const seqs = new Set<number>()
    let contiguous = 0

    const closeStream = () => {
      es?.close()
      es = null
    }
    const stopAll = () => {
      closeStream()
      clearTimeout(pollTimer)
      clearTimeout(refreshTimer)
    }
    const expire = () => {
      terminal = true
      stopAll()
      dispatch({ type: 'connection', connection: 'expired' })
    }

    const refresh = async (): Promise<ExecutionRun | null> => {
      const id = ++refreshIssued
      try {
        const [run, result] = await Promise.all([fetchRun(runId), fetchRunResult(runId)])
        if (disposed || id < refreshApplied) return run
        refreshApplied = id
        dispatch({ type: 'run', run })
        dispatch({ type: 'result', result })
        dispatch({ type: 'error', error: null })
        return run
      } catch (e) {
        if (disposed) return null
        if (e instanceof ApiError && e.status === 404) expire()
        else dispatch({ type: 'error', error: (e as Error).message })
        return null
      }
    }
    refreshRef.current = () => void refresh()

    const scheduleRefresh = () => {
      if (refreshTimer !== undefined) {
        refreshPending = true
        return
      }
      void refresh()
      refreshTimer = setTimeout(() => {
        refreshTimer = undefined
        if (refreshPending && !disposed) {
          refreshPending = false
          scheduleRefresh()
        }
      }, REFRESH_MS)
    }

    const finish = () => {
      if (terminal) return
      terminal = true
      stopAll()
      dispatch({ type: 'connection', connection: 'closed' })
      void refresh() // final, unthrottled
    }

    const ingest = (events: ExecutionEvent[]) => {
      if (disposed) return
      const fresh = events.filter((e) => e.run_id === runId && !seqs.has(e.seq))
      if (!fresh.length) return
      fresh.forEach((e) => seqs.add(e.seq))
      while (seqs.has(contiguous + 1)) contiguous++
      dispatch({ type: 'events', events: fresh })
      if (fresh.some((e) => TERMINAL_EVENT_TYPES.includes(e.type))) finish()
      else scheduleRefresh()
    }

    const backfill = async () => {
      const events = await fetchRunEvents(runId, contiguous)
      ingest(events)
    }

    const onEvent = (msg: MessageEvent<string>) => {
      let event: ExecutionEvent
      try {
        event = JSON.parse(msg.data) as ExecutionEvent
      } catch {
        return // malformed frame; the gap check on the next event recovers it
      }
      const gap = event.seq > contiguous + 1
      ingest([event])
      if (gap && !terminal) void backfill().catch(() => {})
    }

    const startPolling = () => {
      closeStream()
      dispatch({ type: 'connection', connection: 'polling' })
      const tick = async () => {
        if (disposed || terminal) return
        try {
          await backfill()
          const run = await refresh()
          if (isTerminal(run)) finish()
        } catch (e) {
          if (e instanceof ApiError && e.status === 404) return expire()
          dispatch({ type: 'error', error: (e as Error).message })
        }
        if (!disposed && !terminal) pollTimer = setTimeout(tick, POLL_MS)
      }
      void tick()
    }

    const openStream = () => {
      const source = new EventSource(runStreamUrl(runId))
      es = source
      for (const t of EVENT_TYPES) source.addEventListener(t, onEvent as EventListener)
      source.onopen = () => {
        sseErrors = 0
        if (!terminal) dispatch({ type: 'connection', connection: 'live' })
      }
      source.onerror = () => {
        if (disposed || terminal || es !== source) return
        sseErrors++
        // CLOSED: the server refused the stream (e.g. 404), so the browser will not retry.
        if (source.readyState === ES_CLOSED || sseErrors >= MAX_SSE_ERRORS) startPolling()
        else dispatch({ type: 'connection', connection: 'reconnecting' })
      }
    }

    void (async () => {
      let run: ExecutionRun
      try {
        run = await fetchRun(runId)
      } catch (e) {
        if (disposed) return
        if (e instanceof ApiError && e.status === 404) return expire()
        dispatch({ type: 'error', error: (e as Error).message })
        dispatch({ type: 'connection', connection: 'error' })
        return
      }
      if (disposed) return
      dispatch({ type: 'run', run })
      if (isTerminal(run)) {
        // Finished run: load its events once; no stream, so no reconnect loop.
        try {
          await backfill()
        } catch {
          /* the run and result below are still shown */
        }
        finish()
      } else {
        void refresh()
        openStream()
      }
    })()

    return () => {
      disposed = true
      stopAll()
      refreshRef.current = () => {}
    }
  }, [runId])

  return { ...view, refresh: () => refreshRef.current() }
}
