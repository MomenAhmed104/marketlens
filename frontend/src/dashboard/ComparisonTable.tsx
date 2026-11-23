// Competitor comparison for one product, grouped by match status with a coloured
// rail per group. Prices and differences are shown exactly as the backend computed
// them: a difference only when comparison.eligible, never a percentage or saving.
// Direct competitor prices (exact matches) and alternative-product prices
// (equivalent variants, similar alternatives) are shown in separate sections and
// never mixed: alternative prices have no difference column.
import { useState } from 'react'
import type { CandidateMatch, CompetitorProduct, MatchStatus } from '../api'
import ProductThumb from '../components/ProductThumb'
import StatusPill from '../components/StatusPill'
import { diffDirection, label, money, plural, signedGbp } from '../format'
import { explain } from '../plainLanguage'
import { statusMeta } from '../status'
import EvidenceDrawer from './EvidencePanel'

type Kind = 'direct' | 'alternative' | 'other'

const GROUPS: { status: MatchStatus; title: string; note: string; kind: Kind }[] = [
  { status: 'EXACT_MATCH', title: 'Exact matches', note: 'Same product identity and required variants.', kind: 'direct' },
  {
    status: 'REVIEW_REQUIRED',
    title: 'Review required',
    note: 'Possible match with unresolved identity or variant evidence.',
    kind: 'direct',
  },
  {
    status: 'EQUIVALENT_VARIANT',
    title: 'Equivalent variants',
    note: 'Otherwise the same product; only the cosmetic finish or colour differs. Not a price benchmark.',
    kind: 'alternative',
  },
  {
    status: 'SIMILAR_MATCH',
    title: 'Similar alternatives',
    note: 'Related product or offer, not the same product. Never price-compared.',
    kind: 'alternative',
  },
  {
    status: 'NO_MATCH',
    title: 'No match',
    note: 'Evidence does not support a match (for example guides, forums or category pages).',
    kind: 'other',
  },
]

interface Props {
  candidates: CandidateMatch[]
  pages: Map<string, CompetitorProduct>
  stompboxPrice: string | null
  stompboxCurrency: string
}

export default function ComparisonTable(props: Props) {
  const [open, setOpen] = useState<CandidateMatch | null>(null)
  const groupsOf = (kind: Kind) =>
    GROUPS.filter((g) => g.kind === kind)
      .map((g) => ({ ...g, rows: props.candidates.filter((c) => c.match_status === g.status) }))
      .filter((g) => g.rows.length > 0)
  const render = (g: ReturnType<typeof groupsOf>[number]) => {
    const cls = `group group-${g.status.toLowerCase()}`
    const head = (
      <>
        <span className={`group-dot seg-${g.status.toLowerCase()}`} aria-hidden="true" />
        <span className="group-title">
          {g.title} <span className="group-count">{g.rows.length}</span>
        </span>
        <span className="group-note">{g.note}</span>
      </>
    )
    const table = <GroupTable {...props} rows={g.rows} alternative={g.kind === 'alternative'} onEvidence={setOpen} />
    return g.status === 'NO_MATCH' ? (
      <details key={g.status} className={cls}>
        <summary className="group-head">{head}</summary>
        {table}
      </details>
    ) : (
      <section key={g.status} className={cls} aria-label={`${g.title} (${g.rows.length})`}>
        <h3 className="group-head">{head}</h3>
        {table}
      </section>
    )
  }
  const alternatives = groupsOf('alternative')
  return (
    <div className="groups">
      {groupsOf('direct').map(render)}
      {alternatives.length > 0 && (
        <section className="alt-products" aria-label="Alternative products">
          <h3 className="drawer-section">Alternative products</h3>
          <p className="hint">
            Alternative-product prices are shown for information only. They are never price benchmarks, are never compared
            with the Stompbox price and are kept separate from exact-match prices.
          </p>
          {alternatives.map(render)}
        </section>
      )}
      {groupsOf('other').map(render)}
      {open && (
        <EvidenceDrawer
          m={open}
          page={props.pages.get(open.candidate_id)}
          stompboxPrice={props.stompboxPrice}
          stompboxCurrency={props.stompboxCurrency}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  )
}

function Proposal({ m }: { m: CandidateMatch }) {
  if (!m.proposed_match) return null
  return (
    <span className="cell-sub">
      LLM 2 proposed {statusMeta(m.proposed_match).label.toLowerCase()}
      {m.proposal_agreement && m.proposal_agreement !== 'AGREES' && ` · ${statusMeta(m.proposal_agreement).label}`}
    </span>
  )
}

function GroupTable({
  rows,
  pages,
  stompboxPrice,
  stompboxCurrency,
  alternative,
  onEvidence,
}: Props & { rows: CandidateMatch[]; alternative: boolean; onEvidence: (m: CandidateMatch) => void }) {
  return (
    <div className="table-scroll">
      <table className="data compare">
        <thead>
          <tr>
            <th scope="col">Competitor</th>
            <th scope="col" className="col-thumb">
              <span className="sr-only">Listing image</span>
            </th>
            <th scope="col">Page</th>
            <th scope="col">Condition</th>
            <th scope="col">Availability</th>
            <th scope="col" className="num">
              {alternative ? 'Alternative-product price' : 'Competitor price'}
            </th>
            {!alternative && (
              <th scope="col" className="num">
                Difference (competitor − Stompbox)
              </th>
            )}
            <th scope="col" className="col-action">
              <span className="sr-only">Evidence</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => {
            const page = pages.get(m.candidate_id)
            const title = page?.page.title ?? page?.search.title ?? m.product_url
            const compared = m.comparison.eligible && m.comparison.difference_gbp !== null
            const alt = m.alternative_price
            return (
              <tr key={m.candidate_id}>
                <th scope="row">
                  <span className="cell-strong">{m.competitor.company_name ?? m.competitor.domain}</span>
                  <span className="cell-mono">{m.competitor.domain}</span>
                  <span className="tags">
                    {m.competitor.approval_status !== 'PRECONFIGURED' && <StatusPill value={m.competitor.approval_status} />}
                    {!m.competitor.price_benchmark && <span className="bench no">Not a price benchmark</span>}
                  </span>
                </th>
                <td className="col-thumb">
                  <ProductThumb image={page?.image} alt={`${m.competitor.company_name ?? m.competitor.domain}: ${title}`} size="sm" />
                </td>
                <td className="wrap">
                  <a href={m.product_url} target="_blank" rel="noreferrer noopener">
                    {title}
                  </a>
                  <span className="cell-sub">
                    <StatusPill value={m.match_status} /> {label(m.match_confidence)} confidence
                  </span>
                  <Proposal m={m} />
                </td>
                <td>
                  {alt?.condition_label ?? label(m.condition.condition)}
                  {!m.condition.comparable_to_new && <span className="cell-sub text-warning">not comparable to new</span>}
                </td>
                <td>
                  <StatusPill value={m.availability?.status ?? 'UNKNOWN'} />
                </td>
                {alternative ? (
                  <td className="num">
                    {alt?.shown && alt.price !== null ? (
                      <span className="price-main">{money(alt.price, alt.currency)}</span>
                    ) : (
                      <>
                        <span className="text-muted">Not shown</span>
                        {(alt?.reasons[0] ?? m.price.reasons[0]) && (
                          <span className="cell-sub">{explain(alt?.reasons[0] ?? m.price.reasons[0])}</span>
                        )}
                      </>
                    )}
                    <StatusPill value={m.price.status} />
                    <span className="cell-sub">Information only · not benchmarked</span>
                  </td>
                ) : (
                  <>
                    <td className="num">
                      {m.price.price !== null && <span className="price-main">{money(m.price.price, m.price.currency)}</span>}
                      <StatusPill value={m.price.status} />
                      <span className="cell-sub">
                        Stompbox{' '}
                        {stompboxPrice === null ? <span className="text-warning">missing</span> : money(stompboxPrice, stompboxCurrency)}
                      </span>
                    </td>
                    <td className="num">
                      {compared ? (
                        <span className={`diff-chip ${Number(m.comparison.difference_gbp) < 0 ? 'lower' : 'higher'}`}>
                          <strong>{signedGbp(m.comparison.difference_gbp!)}</strong>
                          <span>{diffDirection(m.comparison.difference_gbp!)}</span>
                        </span>
                      ) : (
                        <>
                          <span className="text-muted">Not compared</span>
                          {m.comparison.reasons[0] && <span className="cell-sub">{explain(m.comparison.reasons[0])}</span>}
                        </>
                      )}
                    </td>
                  </>
                )}
                <td className="col-action">
                  <button
                    type="button"
                    className="btn small-btn"
                    aria-label={`Evidence for ${m.competitor.domain}`}
                    aria-haspopup="dialog"
                    onClick={() => onEvidence(m)}
                  >
                    Evidence
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="sr-only">{plural(rows.length, 'candidate')}</p>
    </div>
  )
}
