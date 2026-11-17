// Evidence for one candidate in a drawer with three tabs: Source, Identity, Price.
import { useState } from 'react'
import type { AttributeCheck, CandidateMatch, CompetitorProduct } from '../api'
import Drawer from '../components/Drawer'
import { ExternalIcon } from '../components/icons'
import StatusPill from '../components/StatusPill'
import Tabs, { TabPanel } from '../components/Tabs'
import { label, money, signedGbp, when } from '../format'

type Tab = 'source' | 'identity' | 'price'

interface Props {
  m: CandidateMatch
  page: CompetitorProduct | undefined
  stompboxPrice: string | null
  stompboxCurrency: string
  onClose: () => void
}

const CHECK_ICON: Record<string, string> = { MATCH: '✓', CONFLICT: '✕', UNVERIFIED: '?', NOT_APPLICABLE: '–' }

export default function EvidenceDrawer({ m, page, stompboxPrice, stompboxCurrency, onClose }: Props) {
  // Review rows open on identity (what must be resolved); others on price.
  const [tab, setTab] = useState<Tab>(m.match_status === 'REVIEW_REQUIRED' ? 'identity' : 'price')
  const checks: AttributeCheck[] = [m.brand, m.model, ...m.variants.filter((v) => v.result !== 'NOT_APPLICABLE')]

  return (
    <Drawer
      title={m.competitor.company_name ?? m.competitor.domain}
      subtitle={
        <>
          <StatusPill value={m.match_status} /> <span className="cell-mono">{m.competitor.domain}</span>
        </>
      }
      onClose={onClose}
    >
      <Tabs
        label="Evidence sections"
        idPrefix="evidence"
        value={tab}
        onChange={setTab}
        tabs={[
          { id: 'price', label: 'Price' },
          { id: 'identity', label: 'Identity' },
          { id: 'source', label: 'Source' },
        ]}
      />

      {tab === 'price' && (
        <TabPanel idPrefix="evidence" id="price">
          <div className="receipt">
            <div className="receipt-row">
              <span>Status</span>
              <span>
                <StatusPill value={m.price.status} /> <StatusPill value={m.price.evidence_status} />
              </span>
            </div>
            <div className="receipt-row">
              <span>Displayed price</span>
              <strong>{m.price.price_text ?? <span className="text-muted">none extracted</span>}</strong>
            </div>
            {m.price.quote && (
              <figure className="quote">
                <blockquote>{m.price.quote}</blockquote>
                <figcaption>Verbatim page text</figcaption>
              </figure>
            )}
            <div className="receipt-row">
              <span>Source</span>
              <a href={m.price.source_url} target="_blank" rel="noreferrer noopener" className="break">
                {m.price.source_url}
              </a>
            </div>
            <div className="receipt-row">
              <span>Retrieved</span>
              <span>{when(m.price.retrieved_at)}</span>
            </div>
            {m.price.sale_indicated && (
              <p className="note">The page shows sale or “was” wording. Only the current displayed price is used.</p>
            )}
          </div>
          {m.price.reasons.length > 0 && (
            <ul className="plain-list">
              {m.price.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
          <h3 className="drawer-section">Comparison</h3>
          {m.comparison.eligible && m.comparison.difference_gbp !== null ? (
            <p className="equation">
              Competitor {money(m.comparison.competitor_price, 'GBP')} − Stompbox{' '}
              {money(m.comparison.stompbox_price, m.comparison.stompbox_currency)} ={' '}
              <strong>{signedGbp(m.comparison.difference_gbp)}</strong> (calculated by the backend).
            </p>
          ) : (
            <>
              <p className="hint">
                Stompbox price:{' '}
                {stompboxPrice === null ? <span className="text-warning">missing</span> : money(stompboxPrice, stompboxCurrency)}.
                Not compared because:
              </p>
              <ul className="plain-list">
                {m.comparison.reasons.map((r) => (
                  <li key={r}>Not compared: {r}</li>
                ))}
              </ul>
            </>
          )}
        </TabPanel>
      )}

      {tab === 'identity' && (
        <TabPanel idPrefix="evidence" id="identity">
          <h3 className="drawer-section">Why this match status</h3>
          <ul className="plain-list">
            {m.match_reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
          {m.review_reasons.length > 0 && (
            <div className="callout warning">
              <h3 className="drawer-section">To resolve</h3>
              <ul className="plain-list">
                {m.review_reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
          )}
          <h3 className="drawer-section">Identity checks</h3>
          <ul className="checklist">
            {checks.map((c) => (
              <li key={c.attribute} className={`check-item check-${c.result.toLowerCase()}`}>
                <span className="check-mark" aria-hidden="true">
                  {CHECK_ICON[c.result] ?? '?'}
                </span>
                <div>
                  <div className="check-head">
                    <span className="check-name">{label(c.attribute)}</span>
                    {c.critical && <span className="tag">critical</span>}
                    <StatusPill value={c.result} />
                  </div>
                  <dl className="check-values">
                    <dt>Stompbox</dt>
                    <dd>{c.expected ?? 'n/a'}</dd>
                    <dt>Competitor</dt>
                    <dd className="break">{c.found.join(' · ') || 'none found'}</dd>
                  </dl>
                  {c.note && <p className="hint">{c.note}</p>}
                </div>
              </li>
            ))}
          </ul>
          <p className="hint">
            Condition: {label(m.condition.condition)}
            {m.condition.note && ` · ${m.condition.note}`}
          </p>
        </TabPanel>
      )}

      {tab === 'source' && (
        <TabPanel idPrefix="evidence" id="source">
          <dl className="facts">
            <dt>Page</dt>
            <dd>
              <a href={m.product_url} target="_blank" rel="noreferrer noopener" className="break">
                {m.product_url} <ExternalIcon />
              </a>
            </dd>
            {page && (
              <>
                <dt>Retrieval</dt>
                <dd>
                  <StatusPill value={page.page.status ?? 'NOT_FOUND'} text={page.page.status ? undefined : 'Not fetched'} />{' '}
                  via {page.page.provider} · {when(page.page.retrieved_at)}
                  {page.page.cache_source && ` · content source: ${page.page.cache_source}`}
                  {page.page.error && <span className="cell-sub text-danger">{page.page.error}</span>}
                </dd>
                <dt>Found by</dt>
                <dd>
                  “{page.search.query_text}” · rank {page.search.rank} · {page.search.provider} ·{' '}
                  {when(page.search.retrieved_at)}
                </dd>
              </>
            )}
            <dt>Extraction</dt>
            <dd>
              <StatusPill value={m.extraction_status} />
              {page?.extraction_error && <span className="cell-sub">{page.extraction_error}</span>}
            </dd>
            <dt>Page type</dt>
            <dd>
              {label(m.page.kind)} · {m.page.basis}
            </dd>
          </dl>
          <p className="note">Retrieval time is not the retailer's last update time.</p>
        </TabPanel>
      )}
    </Drawer>
  )
}
