import { useRunContext } from '../app/runContext'
import EmptyState from '../components/EmptyState'
import StatusPill from '../components/StatusPill'
import CataloguePicker from '../dashboard/CataloguePicker'
import EventTimeline from '../dashboard/EventTimeline'
import PipelineStepper, { PipelineTrack } from '../dashboard/PipelineStepper'
import RunControls from '../dashboard/RunControls'
import { DiscoveredDomainsTable } from '../dashboard/RunDomains'
import { CancelControl, LiveCounters, ProductProgress, RunSummary } from '../dashboard/RunPanels'
import { plural, when } from '../format'
import { isTerminal } from '../runStream'

export default function LiveRuns() {
  const ctx = useRunContext()
  const { runId, view, active, catalogue } = ctx
  const run = view.run
  const noProvider = !!ctx.intelCfg && !ctx.intelCfg.search_provider

  const newRun = (
    <section className="new-run" aria-labelledby="new-run-heading">
      <div className="section-head">
        <h2 id="new-run-heading">{runId ? 'Start another run' : 'Start a run'}</h2>
        <span className="hint-inline">Choose up to 5 products. Runs are kept in memory only.</span>
      </div>
      <CataloguePicker
        products={ctx.products}
        error={ctx.catalogueError}
        selected={ctx.selected}
        onChange={ctx.setSelected}
        disabled={active || ctx.starting}
      />
      <RunControls
        selectedCount={ctx.selected.length}
        targets={ctx.intelCfg?.search_targets ?? null}
        active={active}
        starting={ctx.starting}
        noProvider={noProvider}
        startError={ctx.startError}
        onStart={(opts) => void ctx.start({ product_ids: ctx.selected, ...opts })}
      />
    </section>
  )

  if (!runId) {
    return (
      <div className="stack">
        <EmptyState title="No run selected">
          <p>Select products below and start a run, or pick a recent run from the run switcher.</p>
        </EmptyState>
        {newRun}
      </div>
    )
  }

  if (view.connection === 'expired') {
    return (
      <div className="stack">
        <div className="callout warning" role="alert">
          <p>
            Run <code>{runId}</code> is no longer available. Runs are kept in memory only: they expire after a while and
            are cleared when the backend restarts.
          </p>
          <button type="button" className="btn" onClick={() => ctx.openRun(null)}>
            Close run
          </button>
        </div>
        {newRun}
      </div>
    )
  }

  if (!run) {
    return (
      <div className="stack">
        {view.connection === 'error' ? (
          <div className="callout danger" role="alert">
            <p>
              Could not load run {runId}: {view.error}
            </p>
            <button type="button" className="btn" onClick={() => ctx.openRun(null)}>
              Close run
            </button>
          </div>
        ) : (
          <p className="hint">Loading run…</p>
        )}
        {newRun}
      </div>
    )
  }

  const terminal = isTerminal(run)
  const discovered = view.result?.discovery?.candidates ?? []
  return (
    <div className="stack">
      <section className="run-header" aria-labelledby="run-heading">
        <div className="run-header-top">
          <div>
            <p className="eyebrow">
              Run <code>{run.run_id}</code>
            </p>
            <h2 id="run-heading" className="run-title">
              {run.request.product_ids.map((id) => catalogue.get(id)?.product_name ?? id).join(', ')}
            </h2>
            <p className="hint">
              {run.request.product_ids.join(', ')} · started {when(run.started_at ?? run.created_at)}
              {run.finished_at && ` · finished ${when(run.finished_at)}`}
              {run.request.domains && ` · limited to ${plural(run.request.domains.length, 'domain')}`}
              {!run.request.run_discovery && ' · discovery off'}
            </p>
          </div>
          <div className="header-actions">
            <span className="status-lg">
              <StatusPill value={run.status} />
            </span>
            {active && <CancelControl onCancel={() => void ctx.cancel()} busy={ctx.cancelling} requested={run.cancel_requested} />}
            {terminal && (
              <button type="button" className="btn primary" onClick={() => void ctx.start(run.request)} disabled={ctx.starting}>
                Re-run these products
              </button>
            )}
          </div>
        </div>
        <PipelineTrack stages={run.stages} />
        {ctx.cancelError && (
          <p role="alert" className="callout danger">
            {ctx.cancelError}
          </p>
        )}
        {terminal && ctx.startError && (
          <p role="alert" className="callout danger">
            Could not start the run: {ctx.startError}
          </p>
        )}
        {terminal && ctx.approvedSinceRun.length > 0 && run.request.domains === null && (
          <p className="callout success">
            A re-run will also search {plural(ctx.approvedSinceRun.length, 'domain')} approved since this run:{' '}
            {ctx.approvedSinceRun.join(', ')}.
          </p>
        )}
        {view.error && <p className="hint text-warning">Update failed: {view.error}</p>}
      </section>

      {terminal && <RunSummary run={run} result={view.result} />}

      <div className="grid-12">
        <section className="panel-flat span-7" aria-labelledby="pipeline-heading">
          <div className="section-head">
            <h2 id="pipeline-heading">Pipeline</h2>
            <span className="hint-inline">7 stages</span>
          </div>
          <PipelineStepper stages={run.stages} />
        </section>
        <div className="span-5 stack">
          <section className="panel-flat" aria-labelledby="products-heading">
            <div className="section-head">
              <h2 id="products-heading">Products</h2>
              <span className="hint-inline">
                {run.summary.products_completed}/{run.summary.products_total} completed
              </span>
            </div>
            <ProductProgress result={view.result} run={run} catalogue={catalogue} />
          </section>
          <section className="panel-flat" aria-labelledby="counters-heading">
            <div className="section-head">
              <h2 id="counters-heading">{terminal ? 'Results' : 'Results so far'}</h2>
            </div>
            <LiveCounters run={run} />
          </section>
        </div>
      </div>

      {discovered.length > 0 && (
        <section className="panel-flat" aria-labelledby="discovered-heading">
          <div className="section-head">
            <h2 id="discovered-heading">Discovered domains</h2>
            <span className="hint-inline">{plural(discovered.length, 'domain')}</span>
          </div>
          <p className="hint">
            Retailers that pass the basic checks are searched automatically (never as price benchmarks). Other
            domains stay pending and are not searched until approved on Domain Review; a rejection always wins.
          </p>
          <DiscoveredDomainsTable candidates={discovered} />
        </section>
      )}

      <section className="panel-flat" aria-labelledby="timeline-heading">
        <div className="section-head">
          <h2 id="timeline-heading">Event feed</h2>
        </div>
        <EventTimeline events={view.events} />
      </section>

      {newRun}
    </div>
  )
}
