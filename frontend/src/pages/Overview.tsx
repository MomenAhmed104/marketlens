// Overview of the selected run. Every number comes from the run's backend results
// (selectors.ts, docs/dashboard.md); no trends, percentages or price arithmetic.
import type { MatchStatus, PipelineResult, StompboxProduct } from '../api'
import { href } from '../app/router'
import { useRunContext } from '../app/runContext'
import DistributionBar from '../components/DistributionBar'
import EmptyState from '../components/EmptyState'
import { ExternalIcon, PriceIcon } from '../components/icons'
import StatTile from '../components/StatTile'
import StatusPill from '../components/StatusPill'
import { diffDirection, money, plural, signedGbp, when } from '../format'
import {
  coverageRows,
  eligibleComparisons,
  keyFinding,
  MATCH_STATUSES,
  overviewKpis,
  priceGate,
  productInsights,
  type PriceGate,
} from '../selectors'
import { statusMeta } from '../status'

export default function Overview() {
  const { runId, view, catalogue } = useRunContext()
  const { run, result } = view

  if (!runId || view.connection === 'expired') {
    return (
      <EmptyState
        title={runId ? 'This run is no longer available' : 'No run selected'}
        action={
          <a className="btn primary" href={href('runs')}>
            Start or open a run
          </a>
        }
      >
        <p>
          The overview summarises one run. Runs are kept in backend memory only, so they disappear after a while or when
          the backend restarts.
        </p>
      </EmptyState>
    )
  }
  if (!run) return <p className="hint">Loading run…</p>

  const k = overviewKpis(run, result)
  const finding = keyFinding(run, result)
  const comparisons = eligibleComparisons(result)
  const insights = productInsights(result)
  const attention = insights.noCandidates.length + insights.noExactMatch.length + insights.failed.length
  const finished = run.status !== 'RUNNING' && run.status !== 'PENDING'
  const counts: Record<MatchStatus, number> = {
    EXACT_MATCH: run.summary.exact_matches,
    REVIEW_REQUIRED: run.summary.review_required,
    SIMILAR_MATCH: run.summary.similar_matches,
    NO_MATCH: run.summary.no_match,
  }
  const byApproval = Object.entries(k.searchedByApproval)
    .map(([s, c]) => `${c} ${statusMeta(s).label.toLowerCase()}`)
    .join(' · ')
  const firstProduct = run.request.product_ids[0]

  return (
    <div className="stack">
      <section className={`hero hero-${finding.tone}`} aria-labelledby="hero-heading">
        <div className="hero-main">
          <p className="hero-eyebrow">
            Selected run <code>{run.run_id}</code>
          </p>
          <p className="hero-products">{productLine(run.request.product_ids, catalogue)}</p>
          <h2 id="hero-heading" className="hero-headline">
            {finding.headline}
          </h2>
          <p className="hero-detail">{finding.detail}</p>
          {finished && attention === 0 && (
            <p className="hero-ok">
              <span aria-hidden="true">✓</span> Every completed product has an exact match.
            </p>
          )}
          <div className="hero-actions">
            <a className="btn on-dark" href={href('products', firstProduct)}>
              Open product results
            </a>
            <a className="btn ghost-dark" href={href('runs')}>
              Run details
            </a>
          </div>
        </div>
        <dl className="hero-meta">
          <div>
            <dt>Status</dt>
            <dd>
              <StatusPill value={run.status} />
            </dd>
          </div>
          <div>
            <dt>Started</dt>
            <dd>{when(run.started_at ?? run.created_at)}</dd>
          </div>
          <div>
            <dt>Discovery</dt>
            <dd>{!run.request.run_discovery ? 'Off' : result?.discovery?.mode === 'LLM_ENABLED' ? 'LLM 1' : 'No LLM'}</dd>
          </div>
          <div>
            <dt>Extraction</dt>
            <dd>{result?.intelligence_mode === 'LLM_ENABLED' ? 'LLM 2' : result ? 'Off' : '…'}</dd>
          </div>
        </dl>
      </section>

      <div className="grid-12">
        <section className="panel-flat span-8" aria-labelledby="dist-heading">
          <div className="section-head">
            <h2 id="dist-heading">Match distribution</h2>
            <span className="hint-inline">{plural(run.summary.candidates, 'candidate')} across all products</span>
          </div>
          <DistributionBar counts={counts} caption={`Candidates by match status (${run.summary.candidates} total)`} />
          {result && result.products.length > 1 && (
            <ul className="dist-products">
              {result.products.map((p) => (
                <li key={p.product_id}>
                  <a href={href('products', p.product_id)} className="dist-product-name">
                    {p.product_id}
                  </a>
                  {p.matching ? (
                    <DistributionBar
                      compact
                      counts={p.matching.counts}
                      caption={`${p.product_id} candidates by match status`}
                    />
                  ) : (
                    <StatusPill value={p.status} />
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="span-4 stat-grid-2" aria-label="Key figures">
          <StatTile
            label="Products analysed"
            value={`${k.productsCompleted}/${k.productsTotal}`}
            detail={k.productsFailed ? plural(k.productsFailed, 'failed product') : 'completed'}
            tone={k.productsFailed ? 'attention' : 'default'}
            source="run.summary.products_completed, products_total"
          />
          <StatTile
            label="Competitors searched"
            value={k.competitorsSearched}
            detail={byApproval || 'no targets yet'}
            source="result.search_targets"
          />
          <StatTile
            label="Needs review"
            value={k.productsNeedingReview}
            detail={`${plural(k.productsNeedingReview, 'product')} · ${plural(k.reviewCandidates, 'candidate')}`}
            tone={k.productsNeedingReview > 0 ? 'attention' : 'default'}
            source="products with matching.counts.REVIEW_REQUIRED > 0; run.summary.review_required"
          />
          <StatTile
            label="Eligible prices"
            value={k.comparablePrices > 0 ? k.comparablePrices : 'None'}
            detail={
              k.comparablePrices > 0
                ? plural(k.comparablePrices, 'comparison')
                : result?.intelligence_mode === 'LLM_DISABLED'
                  ? 'Unavailable: extraction off'
                  : 'none eligible'
            }
            tone={k.comparablePrices > 0 ? 'accent' : 'muted'}
            source="run.summary.comparable_prices"
          />
        </section>

        <section
          className={`panel-flat ${attention > 0 ? 'span-7' : 'span-12'}`}
          aria-labelledby="prices-heading"
        >
          <div className="section-head">
            <h2 id="prices-heading">Price comparisons</h2>
            <span className="hint-inline">Competitor − Stompbox, calculated by the backend</span>
          </div>
          {comparisons.length > 0 ? (
            <ul className="price-cards">
              {comparisons.map(({ productId, productName, candidate: c }) => (
                <li key={c.candidate_id} className="price-card">
                  <div className="price-card-head">
                    <span className="price-card-competitor">{c.competitor.company_name ?? c.competitor.domain}</span>
                    <a href={href('products', productId)} className="price-card-product">
                      {productId} · {productName}
                    </a>
                  </div>
                  <div className="price-card-figures">
                    <div>
                      <span className="fig-label">Competitor</span>
                      <span className="fig-value">{money(c.comparison.competitor_price, 'GBP')}</span>
                    </div>
                    <div>
                      <span className="fig-label">Stompbox</span>
                      <span className="fig-value">{money(c.comparison.stompbox_price, c.comparison.stompbox_currency)}</span>
                    </div>
                    <div className="fig-diff">
                      <span className="fig-label">Difference</span>
                      <span className="fig-value">{signedGbp(c.comparison.difference_gbp!)}</span>
                      <span className="fig-note">{diffDirection(c.comparison.difference_gbp!)}</span>
                    </div>
                  </div>
                  <a href={c.product_url} target="_blank" rel="noreferrer noopener" className="source-link">
                    Source page <ExternalIcon />
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <PriceGateExplainer gate={priceGate(run, result)} running={!finished} />
          )}
        </section>

        {attention > 0 && (
          <section className="panel-flat span-5" aria-labelledby="attention-heading">
            <div className="section-head">
              <h2 id="attention-heading">Needs attention</h2>
              <span className="hint-inline">{plural(attention, 'product')} without an exact match</span>
            </div>
            <ul className="attention">
              {insights.failed.map((p) => (
                <li key={p.product_id} className="attention-item tone-rail-danger">
                  <StatusPill value="FAILED" />
                  <span>
                    <strong>{p.product_id}</strong> {p.error ?? 'failed'}
                  </span>
                </li>
              ))}
              {insights.noCandidates.map((p) => (
                <li key={p.product_id} className="attention-item tone-rail-neutral">
                  <a href={href('products', p.product_id)}>
                    <strong>{p.product_id}</strong> {p.matching?.product_name}
                  </a>
                  <span className="hint">
                    No candidate pages on the searched domains. This does not mean it is unavailable.
                  </span>
                </li>
              ))}
              {insights.noExactMatch.map((p) => (
                <li key={p.product_id} className="attention-item tone-rail-warning">
                  <a href={href('products', p.product_id)}>
                    <strong>{p.product_id}</strong> {p.matching?.product_name}
                  </a>
                  <span className="hint">
                    Only{' '}
                    {MATCH_STATUSES.filter((s) => s !== 'EXACT_MATCH' && (p.matching?.counts[s] ?? 0) > 0)
                      .map((s) => `${p.matching!.counts[s]} ${statusMeta(s).label.toLowerCase()}`)
                      .join(', ')}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="panel-flat span-12" aria-labelledby="coverage-heading">
          <div className="section-head">
            <h2 id="coverage-heading">Competitor coverage</h2>
            <span className="hint-inline">Candidates found per searched competitor</span>
          </div>
          <Coverage result={result} />
        </section>
      </div>
    </div>
  )
}

function productLine(ids: string[], catalogue: Map<string, StompboxProduct>) {
  const first = catalogue.get(ids[0])
  const name = first ? `${first.product_name} · ${ids[0]}` : ids[0]
  return ids.length > 1 ? `${name} +${ids.length - 1} more` : name
}

function PriceGateExplainer({ gate, running }: { gate: PriceGate; running: boolean }) {
  if (running) {
    return (
      <EmptyState title="Comparisons appear as products finish" icon={<PriceIcon />} tone="info">
        <p>A comparison needs an exact match with a verified GBP price from a benchmark retailer.</p>
      </EmptyState>
    )
  }
  const steps: { ok: boolean; title: string; detail: string }[] = [
    {
      ok: gate.exactMatches > 0,
      title: 'Exact matches found',
      detail: plural(gate.exactMatches, 'exact match', 'exact matches'),
    },
    {
      ok: gate.llmDisabledReason === null && gate.pricesExtracted > 0,
      title: 'Competitor prices extracted',
      detail:
        gate.llmDisabledReason !== null
          ? `LLM 2 extraction is off (${gate.llmDisabledReason}). Set OPENROUTER_API_KEY and LLM2_MODEL to extract prices.`
          : `${plural(gate.pricesExtracted, 'page')} checked for a price`,
    },
    {
      ok: gate.validPrices > 0,
      title: 'Verified GBP prices',
      detail: plural(gate.validPrices, 'valid price'),
    },
  ]
  return (
    <div className="gate">
      <EmptyState title="No eligible price comparison in this run" icon={<PriceIcon />} tone="warning">
        <p>A price is compared only when every condition below is met.</p>
      </EmptyState>
      <ol className="gate-steps">
        {steps.map((s) => (
          <li key={s.title} className={s.ok ? 'gate-step ok' : 'gate-step blocked'}>
            <span className="gate-icon" aria-hidden="true">
              {s.ok ? '✓' : '✕'}
            </span>
            <span>
              <span className="gate-title">
                {s.title}
                <span className="sr-only">{s.ok ? ': met' : ': not met'}</span>
              </span>
              <span className="gate-detail">{s.detail}</span>
            </span>
          </li>
        ))}
      </ol>
      {gate.blockers.length > 0 && (
        <details className="disclosure">
          <summary>Why the exact matches were not compared</summary>
          <ul className="plain-list">
            {gate.blockers.map((b) => (
              <li key={b.reason}>
                {b.reason} <span className="hint-inline">({plural(b.count, 'candidate')})</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

function Coverage({ result }: { result: PipelineResult | null }) {
  const rows = coverageRows(result)
  if (rows.length === 0) return <p className="hint">Search targets appear once the run reaches the targets stage.</p>
  const found = rows.filter((r) => r.candidates > 0 || r.searchErrors.length > 0)
  const empty = rows.filter((r) => r.candidates === 0 && r.searchErrors.length === 0)
  return (
    <div className="stack-sm">
      {found.length > 0 && (
        <div className="table-scroll">
          <table className="data coverage">
            <thead>
              <tr>
                <th scope="col">Competitor</th>
                <th scope="col">Benchmark</th>
                {MATCH_STATUSES.map((s) => (
                  <th key={s} scope="col" className="num">
                    {statusMeta(s).label}
                  </th>
                ))}
                <th scope="col">Search</th>
              </tr>
            </thead>
            <tbody>
              {found.map((r) => (
                <tr key={r.target.domain}>
                  <th scope="row">
                    <span className="cell-strong">{r.target.company_name ?? r.target.domain}</span>
                    <span className="cell-mono">{r.target.domain}</span>
                    {r.target.approval_status !== 'PRECONFIGURED' && <StatusPill value={r.target.approval_status} />}
                  </th>
                  <td>
                    {r.target.price_benchmark ? (
                      <span className="bench yes">Benchmark</span>
                    ) : (
                      <span className="bench no">Not a benchmark</span>
                    )}
                  </td>
                  {MATCH_STATUSES.map((s) => (
                    <td key={s} className="num">
                      {r.counts[s] > 0 ? (
                        <span className={`count-chip seg-text-${s.toLowerCase()}`}>{r.counts[s]}</span>
                      ) : (
                        <span className="text-faint">0</span>
                      )}
                    </td>
                  ))}
                  <td>
                    {r.searchErrors.length === 0 ? (
                      <span className="text-muted">OK</span>
                    ) : (
                      <span className="text-danger" title={r.searchErrors.join('\n')}>
                        {plural(r.searchErrors.length, 'error')}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {empty.length > 0 && (
        <div className="no-candidates">
          <p className="hint">
            Searched with no candidates ({empty.length}). This does not mean the products are unavailable there.
          </p>
          <ul className="chip-list" aria-label="Competitors searched with no candidates">
            {empty.map((r) => (
              <li key={r.target.domain} className="domain-chip">
                {r.target.company_name ?? r.target.domain}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
