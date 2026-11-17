// App-level run state: one useRunStream (one SSE connection) shared by every page,
// plus start/cancel, the catalogue, configuration and the review queue.
// Logic moved unchanged from the Phase 8 Dashboard component.
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  ApiError,
  cancelRun,
  fetchCandidates,
  fetchDiscoveryConfig,
  fetchIntelligenceConfig,
  fetchProducts,
  listRuns,
  startRun,
  TERMINAL_RUN_STATUSES,
  type CandidateSummary,
  type DiscoveryConfig,
  type IntelligenceConfig,
  type RunRequest,
  type StompboxProduct,
} from '../api'
import { isTerminal, useRunStream } from '../runStream'
import { RunContext, type RunContextValue } from './runContext'

const RUN_PARAM = 'run'

function setRunInUrl(runId: string | null) {
  const url = new URL(window.location.href)
  if (runId) url.searchParams.set(RUN_PARAM, runId)
  else url.searchParams.delete(RUN_PARAM)
  window.history.replaceState(null, '', url)
}

export default function RunProvider({ children }: { children: ReactNode }) {
  const [products, setProducts] = useState<StompboxProduct[] | null>(null)
  const [catalogueError, setCatalogueError] = useState<string | null>(null)
  const [discoveryCfg, setDiscoveryCfg] = useState<DiscoveryConfig | null>(null)
  const [intelCfg, setIntelCfg] = useState<IntelligenceConfig | null>(null)
  const [cfgError, setCfgError] = useState<string | null>(null)
  const [candidates, setCandidates] = useState<CandidateSummary[] | null>(null)
  const [reviewVersion, setReviewVersion] = useState(0)
  const [selected, setSelected] = useState<string[]>([])
  const [runId, setRunId] = useState<string | null>(() => new URLSearchParams(window.location.search).get(RUN_PARAM))
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const userChoseRun = useRef(runId !== null)
  const view = useRunStream(runId)
  const { run } = view

  useEffect(() => {
    fetchProducts().then((r) => setProducts(r.items), (e: Error) => setCatalogueError(e.message))
    fetchDiscoveryConfig().then(setDiscoveryCfg, (e: Error) => setCfgError(e.message))
    // Default selection: the active run, else the most recent run still in memory.
    if (!userChoseRun.current) {
      listRuns().then(
        (runs) => {
          if (userChoseRun.current || runs.length === 0) return
          const pick = runs.find((r) => !TERMINAL_RUN_STATUSES.includes(r.status)) ?? runs[0]
          setRunId(pick.run_id)
        },
        () => {}, // no runs list: the user starts or opens one
      )
    }
  }, [])

  // Eligible search targets and the review queue change when a reviewer decides.
  useEffect(() => {
    fetchIntelligenceConfig().then(setIntelCfg, (e: Error) => setCfgError(e.message))
    fetchCandidates().then(setCandidates, () => setCandidates(null))
  }, [reviewVersion])

  // A run's discovery adds pending candidates: refresh the queue when they appear.
  const pendingKey = view.result?.new_pending_domains.join(',') ?? ''
  useEffect(() => {
    if (pendingKey) fetchCandidates().then(setCandidates, () => {})
  }, [pendingKey])

  useEffect(() => setRunInUrl(runId), [runId])

  // Opening an existing run shows its product selection (adjusted during render, once per run).
  const [syncedRunId, setSyncedRunId] = useState<string | null>(null)
  if (run && run.run_id !== syncedRunId) {
    setSyncedRunId(run.run_id)
    setSelected(run.request.product_ids)
  }

  const catalogue = useMemo(() => new Map((products ?? []).map((p) => [p.product_id, p])), [products])
  const active = !!run && !isTerminal(run) && view.connection !== 'expired'

  // Domains approved after the selected run was created: the next run will search them.
  const approvedSinceRun = useMemo(
    () =>
      run
        ? (candidates ?? [])
            .filter(
              (c) =>
                c.review_status === 'APPROVED' &&
                c.decision !== null &&
                Date.parse(c.decision.decided_at) > Date.parse(run.created_at),
            )
            .map((c) => c.domain)
        : [],
    [candidates, run],
  )

  const openRun = (id: string | null) => {
    userChoseRun.current = true
    setStartError(null)
    setCancelError(null)
    setRunId(id)
  }

  const start = async (body: RunRequest) => {
    userChoseRun.current = true
    setStarting(true)
    setStartError(null)
    setCancelError(null)
    try {
      const created = await startRun(body)
      setRunId(created.run_id)
      return true
    } catch (e) {
      setStartError((e as Error).message)
      return false
    } finally {
      setStarting(false)
    }
  }

  const cancel = async () => {
    if (!runId) return
    setCancelling(true)
    setCancelError(null)
    try {
      await cancelRun(runId)
    } catch (e) {
      setCancelError(
        e instanceof ApiError && e.status === 409 ? 'The run had already finished.' : `Cancel failed: ${(e as Error).message}`,
      )
    } finally {
      setCancelling(false)
      view.refresh()
    }
  }

  const value: RunContextValue = {
    runId,
    view,
    active,
    openRun,
    start,
    starting,
    startError,
    cancel,
    cancelling,
    cancelError,
    selected,
    setSelected,
    products,
    catalogueError,
    catalogue,
    discoveryCfg,
    intelCfg,
    cfgError,
    candidates,
    reviewChanged: () => setReviewVersion((v) => v + 1),
    approvedSinceRun,
  }
  return <RunContext.Provider value={value}>{children}</RunContext.Provider>
}
