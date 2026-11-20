// Evidence for one candidate in a drawer with three tabs: Source, Identity, Price.
// LLM 2's match proposal is shown next to Python's final status; it never replaces it.
import { useState } from 'react'
import type { AttributeCheck, CandidateMatch, CompetitorProduct, IgnoredPriceCategory } from '../api'
import BaselineWarning from '../components/BaselineWarning'
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

const IGNORED_LABEL: Record<IgnoredPriceCategory, string> = {
  SALE: 'Sale / was price',
  DELIVERY: 'Delivery charge',
  FINANCE: 'Finance',
  OTHER: 'Other',
}

export default function EvidenceDrawer({ m, page, stompboxPrice, stompboxCurrency, onClose }: Props) {
  // Review rows open on identity (what must be resolved); others on price.
  const [tab, setTab] = useState<Tab>(m.match_status === 'REVIEW_REQUIRED' ? 'identity' : 'price')
  const checks: AttributeCheck[] = [m.brand, m.model, ...m.variants.filter((v) => v.result !== 'NOT_APPLICABLE')]
  const proposal = page?.match_proposal ?? null
  const uncertainties = m.intelligence?.uncertainties ?? []
  const optionPrices = page?.option_prices ?? []
  const ignoredPrices = page?.ignored_price_details ?? []

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
            {m.price_kind && (
              <div className="receipt-row">
                <span>Price kind</span>
                <span>
                  <StatusPill value={m.price_kind === 'NONE' ? 'NOT_AVAILABLE' : m.price_kind}
                    text={m.price_kind === 'NONE' ? 'Not shown as a competitor price' : undefined} />
                </span>
              </div>
            )}
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
          {ignoredPrices.length > 0 && (
            <>
              <h3 className="drawer-section">Prices not used</h3>
              <ul className="plain-list">
                {ignoredPrices.map((r) => (
                  <li key={r.text}>
                    <span className="tag">{IGNORED_LABEL[r.category]}</span> {r.text}
                  </li>
                ))}
              </ul>
              <p className="hint">Only sale or “was” wording marks a sale; delivery charges never do and are never a product price.</p>
            </>
          )}
          {optionPrices.length > 0 && (
            <>
              <h3 className="drawer-section">Prices shown for options</h3>
              <ul className="plain-list">
                {optionPrices.map((o) => (
                  <li key={`${o.option}-${o.price_text}`}>
                    {o.option}: {o.price_text ?? 'n/a'} <StatusPill value={o.status} />
                    {o.matches_catalogue_variant === true && <span className="tag">matches the Stompbox variant</span>}
                    {o.matches_catalogue_variant === false && (
                      <span className="cell-sub">Alternative option, not the Stompbox variant</span>
                    )}
                    {o.note && <span className="cell-sub">{o.note}</span>}
                  </li>
                ))}
              </ul>
              <p className="hint">
                A price is kept only when exactly one option matches every known Stompbox variant attribute; otherwise it is
                ambiguous. The lowest or default option is never chosen.
              </p>
            </>
          )}
          <h3 className="drawer-section">Comparison</h3>
          {m.alternative_price ? (
            <div className="callout">
              <p>
                <strong>Alternative-product price</strong> ({label(m.match_status)}):{' '}
                {m.alternative_price.shown && m.alternative_price.price !== null
                  ? money(m.alternative_price.price, m.alternative_price.currency)
                  : 'not shown'}
                {m.alternative_price.condition_label && ` · ${m.alternative_price.condition_label}`}
              </p>
              <p className="hint">Shown for information only: never a price benchmark and no difference is calculated.</p>
              {m.alternative_price.reasons.map((r) => (
                <p key={r} className="hint">Not shown: {r}</p>
              ))}
            </div>
          ) : m.comparison.eligible && m.comparison.difference_gbp !== null ? (
            <>
              <p className="equation">
                Competitor {money(m.comparison.competitor_price, 'GBP')} − Stompbox{' '}
                {money(m.comparison.stompbox_price, m.comparison.stompbox_currency)} ={' '}
                <strong>{signedGbp(m.comparison.difference_gbp)}</strong> (calculated by the backend).
              </p>
              <BaselineWarning productId={m.stompbox_product_id} />
            </>
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
              {stompboxPrice !== null && <BaselineWarning productId={m.stompbox_product_id} />}
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
          <h3 className="drawer-section">LLM 2 match proposal</h3>
          {proposal && m.proposal_status === 'COMPLETED' ? (
            <div className="callout">
              <p>
                LLM 2 proposed <StatusPill value={proposal.classification} /> · <StatusPill value={m.proposal_agreement ?? 'NOT_AVAILABLE'} />
              </p>
              <p className="hint">A proposal only; Python's checks set the final status, and a disagreement can only lead to review.</p>
              <ul className="plain-list">
                {proposal.reasons.filter((r) => r.verified).map((r) => (
                  <li key={r.text}>
                    {r.text}
                    {r.quote && <span className="cell-sub">“{r.quote}”</span>}
                  </li>
                ))}
                {proposal.differing_attributes.filter((d) => d.verified).map((d) => (
                  <li key={d.attribute}>
                    {label(d.attribute)} differs{d.cosmetic ? ' (cosmetic)' : ''}: {d.page_value ?? 'n/a'} vs {d.catalogue_value ?? 'n/a'}
                    {d.quote && <span className="cell-sub">“{d.quote}”</span>}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="hint">
              <StatusPill value={m.proposal_status ?? 'NOT_AVAILABLE'} />{' '}
              {page?.match_proposal_error ?? 'No verified LLM 2 proposal; the status rests on Python’s checks.'}
            </p>
          )}
          {uncertainties.length > 0 && (
            <>
              <h3 className="drawer-section">Uncertainties</h3>
              <ul className="plain-list">
                {uncertainties.map((u) => (
                  <li key={u}>{u}</li>
                ))}
              </ul>
            </>
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
            <dt>Availability</dt>
            <dd>
              <StatusPill value={m.availability?.status ?? 'UNKNOWN'} />
              {m.availability?.text && ` “${m.availability.text}”`}
              {m.availability?.reason && <span className="cell-sub">{m.availability.reason}</span>}
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
