import { useEffect, useRef, useState } from 'react'
import type { ExecutionRun, PipelineResult, StompboxProduct } from '../api'
import { href } from '../app/router'
import StatusPill from '../components/StatusPill'
import { plural, usd } from '../format'
import { statusMeta } from '../status'

/** Two-step cancel: nothing is cancelled until the user confirms. */
export function CancelControl(props: { onCancel: () => void; busy: boolean; requested: boolean }) {
  const [confirming, setConfirming] = useState(false)
  const confirmRef = useRef<HTMLButtonElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  const wasConfirming = useRef(false)

  // Focus follows the dialog: to "Confirm cancel" when it opens, back to "Cancel run" on "Keep running".
  useEffect(() => {
    if (confirming) confirmRef.current?.focus()
    else if (wasConfirming.current) triggerRef.current?.focus()
    wasConfirming.current = confirming
  }, [confirming])

  if (props.requested || props.busy) {
    return (
      <button type="button" className="btn danger-outline" disabled>
        Cancelling…
      </button>
    )
  }
  if (!confirming) {
    return (
      <button ref={triggerRef} type="button" className="btn danger-outline" onClick={() => setConfirming(true)}>
        Cancel run
      </button>
    )
  }
  return (
    <div className="confirm" role="group" aria-label="Confirm cancellation">
      <span id="cancel-q">Cancel this run? Finished stages keep their results.</span>
      <button
        ref={confirmRef}
        type="button"
        className="btn danger"
        aria-describedby="cancel-q"
        onClick={() => {
          setConfirming(false)
          props.onCancel()
        }}
      >
        Confirm cancel
      </button>
      <button type="button" className="btn" onClick={() => setConfirming(false)}>
        Keep running
      </button>
    </div>
  )
}

export function ProductProgress(props: { result: PipelineResult | null; run: ExecutionRun; catalogue: Map<string, StompboxProduct> }) {
  const { result, run, catalogue } = props
  const rows = result?.products.length
    ? result.products
    : run.request.product_ids.map((id) => ({ product_id: id, status: 'PENDING' as const, error: null, matching: null }))
  return (
    <ul className="product-cards">
      {rows.map((p) => (
        <li key={p.product_id} className={`product-card rail-${statusMeta(p.status).tone}`}>
          <div className="product-card-head">
            <span className="cell-mono">{p.product_id}</span>
            <StatusPill value={p.status} />
          </div>
          <p className="product-card-name">{p.matching?.product_name ?? catalogue.get(p.product_id)?.product_name ?? ''}</p>
          {p.error && <p className="cell-sub text-danger">{p.error}</p>}
          {p.matching && (
            <div className="product-card-foot">
              <span>
                {plural(p.matching.candidates.length, 'candidate')} · {p.matching.counts.EXACT_MATCH} exact
              </span>
              <a href={href('products', p.product_id)} aria-label={`View results for ${p.product_id}`}>
                View results
              </a>
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}

/** Live counters straight from run.summary (updated as products finish). */
export function LiveCounters({ run }: { run: ExecutionRun }) {
  const s = run.summary
  const items: [string, number, string][] = [
    ['Candidates', s.candidates, 'neutral'],
    ['Exact', s.exact_matches, 'exact_match'],
    ['Review', s.review_required, 'review_required'],
    ['Similar', s.similar_matches, 'similar_match'],
    ['No match', s.no_match, 'no_match'],
    ['Comparable prices', s.comparable_prices, 'neutral'],
  ]
  return (
    <dl className="counters">
      {items.map(([k, v, seg]) => (
        <div key={k}>
          <dt>
            {seg !== 'neutral' && <span className={`dist-dot seg-${seg}`} aria-hidden="true" />}
            {k}
          </dt>
          <dd className="num">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

const OUTCOME: Partial<Record<string, { title: string; text: string }>> = {
  COMPLETED: { title: 'Run completed', text: 'Every stage completed or was deliberately skipped.' },
  PARTIALLY_COMPLETED: {
    title: 'Run partially completed',
    text: 'Some stages, domains or products failed. The results that were produced are kept.',
  },
  FAILED: { title: 'Run failed', text: 'No product completed.' },
  CANCELLED: { title: 'Run cancelled', text: 'Cancelled. Finished stages and products keep their results.' },
}

/** Terminal outcome with counts, usage and warnings. */
export function RunSummary({ run, result }: { run: ExecutionRun; result: PipelineResult | null }) {
  const o = OUTCOME[run.status]
  const disc = result?.discovery?.usage
  const intel = result?.intelligence_usage
  const s = run.summary
  return (
    <div className={`outcome outcome-${statusMeta(run.status).tone}`}>
      {o && (
        <div className="outcome-head">
          <StatusPill value={run.status} />
          <div>
            <p className="outcome-title">{o.title}</p>
            <p className="outcome-text">{o.text}</p>
          </div>
        </div>
      )}
      {run.error && (
        <p role="alert" className="callout danger">
          {run.error}
        </p>
      )}
      <p className="outcome-strip">
        {s.products_completed}/{plural(s.products_total, 'product')} completed
        {s.products_failed > 0 && ` · ${s.products_failed} failed`} · {plural(s.candidates, 'candidate')} ·{' '}
        {plural(s.comparable_prices, 'comparable price')}
      </p>
      {(disc || intel) && (
        <p className="hint">
          Usage (provider-reported or estimated):
          {disc && (
            <>
              {' '}discovery {plural(disc.search_requests, 'search', 'searches')} {usd(disc.search_cost_usd)}, LLM 1{' '}
              {disc.llm_calls ? usd(disc.llm_cost_usd) : 'not used'};
            </>
          )}
          {intel && (
            <>
              {' '}product search {plural(intel.search_requests, 'search', 'searches')} {usd(intel.search_cost_usd)},{' '}
              {intel.pages_ok}/{plural(intel.pages_requested, 'page')} {usd(intel.retrieval_cost_usd)}, LLM 2{' '}
              {intel.llm_calls ? `${plural(intel.llm_calls, 'call')} ${usd(intel.llm_cost_usd)}` : 'not used'}.
            </>
          )}
        </p>
      )}
      {run.warnings.length > 0 && (
        <details className="disclosure">
          <summary>{plural(run.warnings.length, 'warning')}</summary>
          <ul className="plain-list">
            {run.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
