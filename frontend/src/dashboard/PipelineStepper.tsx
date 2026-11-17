import type { StageName, StageState } from '../api'
import StatusPill from '../components/StatusPill'
import { statusMeta } from '../status'

const STAGE_LABELS: Record<StageName, string> = {
  validate: 'Validate selection',
  discovery: 'Competitor discovery',
  review_queue: 'Review queue',
  targets: 'Search targets',
  product_search: 'Search and retrieval',
  extraction: 'Extraction (LLM 2)',
  matching: 'Matching and prices',
}

const timeOf = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : null

/** Compact overview: one segment per stage. Decorative; the timeline carries the meaning. */
export function PipelineTrack({ stages }: { stages: StageState[] }) {
  return (
    <ol className="track" aria-hidden="true">
      {stages.map((st) => (
        <li
          key={st.name}
          className={`track-seg step-${statusMeta(st.status).tone}${st.status === 'RUNNING' ? ' running' : ''}${
            st.status === 'SKIPPED' ? ' step-skipped' : ''
          }`}
        >
          <span className="track-label">{STAGE_LABELS[st.name] ?? st.name}</span>
        </li>
      ))}
    </ol>
  )
}

/** All seven pipeline stages as a vertical timeline with state, progress, message and errors. */
export default function PipelineStepper({ stages }: { stages: StageState[] }) {
  return (
    <ol className="timeline-v" aria-label="Pipeline stages">
      {stages.map((st, i) => {
        const m = statusMeta(st.status)
        const name = STAGE_LABELS[st.name] ?? st.name
        const finished = timeOf(st.finished_at)
        return (
          <li
            key={st.name}
            className={`step step-${m.tone}${st.status === 'SKIPPED' ? ' step-skipped' : ''}`}
            aria-current={st.status === 'RUNNING' ? 'step' : undefined}
          >
            <span className={st.status === 'RUNNING' ? 'step-node spin-ring' : 'step-node'} aria-hidden="true">
              {st.status === 'PENDING' ? i + 1 : m.icon}
            </span>
            <div className="step-body">
              <div className="step-head">
                <span className="step-name">{name}</span>
                <StatusPill value={st.status} />
                {finished && st.status !== 'PENDING' && <span className="step-time">{finished}</span>}
              </div>
              {st.total > 0 && (
                <div className="step-progress">
                  <progress max={st.total} value={st.done} aria-label={`${name}: ${st.done} of ${st.total}`} />
                  <span className="num">
                    {st.done}/{st.total}
                  </span>
                </div>
              )}
              {st.message && <p className="step-message">{st.message}</p>}
              {st.errors.length > 0 && (
                <ul className="step-errors">
                  {st.errors.map((e) => (
                    <li key={e}>{e}</li>
                  ))}
                </ul>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
