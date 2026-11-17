import { createContext, useContext } from 'react'
import type {
  CandidateSummary,
  DiscoveryConfig,
  IntelligenceConfig,
  RunRequest,
  StompboxProduct,
} from '../api'
import type { RunView } from '../runStream'

export interface RunContextValue {
  // selected run (SSE stream lives here, so it survives page navigation)
  runId: string | null
  view: RunView & { refresh: () => void }
  active: boolean
  openRun: (id: string | null) => void
  // run actions
  start: (body: RunRequest) => Promise<boolean>
  starting: boolean
  startError: string | null
  cancel: () => Promise<void>
  cancelling: boolean
  cancelError: string | null
  // product selection for the next run
  selected: string[]
  setSelected: (ids: string[]) => void
  // reference data
  products: StompboxProduct[] | null
  catalogueError: string | null
  catalogue: Map<string, StompboxProduct>
  discoveryCfg: DiscoveryConfig | null
  intelCfg: IntelligenceConfig | null
  cfgError: string | null
  // review queue (persistent, Phase 4)
  candidates: CandidateSummary[] | null
  reviewChanged: () => void
  approvedSinceRun: string[] // approved after the selected run was created
}

export const RunContext = createContext<RunContextValue | null>(null)

export function useRunContext(): RunContextValue {
  const ctx = useContext(RunContext)
  if (!ctx) throw new Error('useRunContext must be used inside RunProvider')
  return ctx
}
