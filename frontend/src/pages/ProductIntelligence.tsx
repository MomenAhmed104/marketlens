import { href } from '../app/router'
import { useRunContext } from '../app/runContext'
import DistributionBar from '../components/DistributionBar'
import EmptyState from '../components/EmptyState'
import { ExternalIcon } from '../components/icons'
import StatusPill from '../components/StatusPill'
import BaselineWarning from '../components/BaselineWarning'
import ComparisonTable from '../dashboard/ComparisonTable'
import ProductThumb from '../components/ProductThumb'
import { revealInRow } from '../components/revealInRow'
import ScrollRow from '../components/ScrollRow'
import { money, plural } from '../format'

export default function ProductIntelligence({ productId }: { productId: string | null }) {
  const { runId, view, catalogue } = useRunContext()
  const result = view.result

  if (!runId || view.connection === 'expired') {
    return (
      <EmptyState
        title={runId ? 'This run is no longer available' : 'No run selected'}
        action={
          <a className="btn primary" href={href('runs')}>
            Go to Live Runs
          </a>
        }
      >
        <p>Product results come from a run. Start one, or pick a recent run from the run switcher.</p>
      </EmptyState>
    )
  }
  if (!result || result.products.length === 0) return <p className="hint">Loading results…</p>

  const pr = result.products.find((p) => p.product_id === productId) ?? result.products[0]
  const product = catalogue.get(pr.product_id)
  const m = pr.matching
  const stompboxPrice = m ? m.stompbox_price : (product?.displayed_price ?? null)
  const currency = product?.currency ?? 'GBP'
  const pages = new Map((pr.intelligence?.candidates ?? []).map((c) => [c.candidate_id, c]))
  const failedDomains = (pr.intelligence?.domains ?? []).filter((d) => d.errors.length > 0)

  return (
    <div className="stack">
      {result.products.length > 1 && (
        <nav aria-label="Products in this run" className="product-switcher">
          <ScrollRow trackClassName="switch-track-row">
            {result.products.map((p) => (
              <a
                key={p.product_id}
                href={href('products', p.product_id)}
                className="switch-item"
                aria-current={p.product_id === pr.product_id ? 'page' : undefined}
                title={catalogue.get(p.product_id)?.product_name}
                ref={p.product_id === pr.product_id ? revealInRow : undefined}
              >
                <span className="cell-mono switch-id">{p.product_id}</span>
                <span className="switch-name">{catalogue.get(p.product_id)?.product_name ?? ''}</span>
                <StatusPill value={p.status} />
              </a>
            ))}
          </ScrollRow>
        </nav>
      )}

      <section className="product-hero" aria-labelledby="product-heading">
        <div className="product-hero-image">
          <ProductThumb image={product?.image} alt={product?.product_name ?? pr.product_id} size="lg" />
          <span className="hint-inline">Stompbox image</span>
        </div>
        <div className="product-hero-main">
          <p className="eyebrow">
            <span className="cell-mono">{pr.product_id}</span>
            {product && ` · ${product.category} · ${product.subcategory}`}
          </p>
          <h2 id="product-heading" className="product-name">
            {m?.product_name ?? product?.product_name ?? pr.product_id}
          </h2>
          <div className="product-facts">
            <span>
              <span className="fact-label">Brand</span> {m?.brand ?? product?.brand ?? '–'}
            </span>
            <span>
              <span className="fact-label">Model</span> {m?.manufacturer_model ?? product?.manufacturer_model ?? '–'}
            </span>
            <StatusPill value={pr.status} />
          </div>
          {m && (
            <div className="product-dist">
              <DistributionBar compact counts={m.counts} caption={`${pr.product_id} candidates by match status`} />
              <span className="hint-inline">
                {plural(m.candidates.length, 'candidate')} · {plural(m.comparable_prices, 'comparable price')}
              </span>
            </div>
          )}
        </div>
        <div className="product-price">
          <span className="fact-label">Stompbox price</span>
          {stompboxPrice === null ? (
            <span className="price-missing">missing (nothing can be compared)</span>
          ) : (
            <span className="price-hero">{money(stompboxPrice, currency)}</span>
          )}
          {product && (
            <span className="hint-inline">
              Snapshot {product.provenance.price_snapshot_date} ·{' '}
              <a href={product.product_url} target="_blank" rel="noreferrer noopener">
                Stompbox page <ExternalIcon />
              </a>
            </span>
          )}
          {stompboxPrice !== null && <BaselineWarning productId={pr.product_id} />}
        </div>
      </section>

      {result.intelligence_mode === 'LLM_DISABLED' && (
        <p className="callout info">
          <strong>Prices not extracted.</strong> Extraction is disabled ({result.llm2_disabled_reason ?? 'LLM 2 not configured'}
          ). Matches rely on page titles and URLs only, so nothing is price-compared.
        </p>
      )}
      {pr.error && (
        <p role="alert" className="callout danger">
          {pr.error}
        </p>
      )}
      {failedDomains.length > 0 && (
        <p className="callout warning">
          Search failed on {failedDomains.map((d) => d.domain).join(', ')}; the other domains were still searched.
        </p>
      )}

      <section aria-labelledby="compare-heading" className="stack-sm">
        <div className="section-head">
          <h2 id="compare-heading">Competitor comparison</h2>
          <span className="hint-inline">Grouped by match status · evidence opens in a side panel</span>
        </div>
        {!m ? (
          <p className="hint">
            {pr.status === 'PENDING' && 'Waiting for its turn…'}
            {pr.status === 'RUNNING' && 'Searching competitor domains and retrieving pages…'}
            {pr.status === 'FAILED' && 'No results for this product.'}
            {pr.status === 'CANCELLED' && 'Cancelled before results were produced.'}
            {pr.status === 'COMPLETED' && 'No matching results.'}
          </p>
        ) : m.candidates.length === 0 ? (
          <EmptyState title="No candidate pages found">
            <p>Nothing was found on the searched domains. This does not mean the product is unavailable from them.</p>
          </EmptyState>
        ) : (
          <ComparisonTable candidates={m.candidates} pages={pages} stompboxPrice={stompboxPrice} stompboxCurrency={currency} />
        )}
      </section>
    </div>
  )
}
