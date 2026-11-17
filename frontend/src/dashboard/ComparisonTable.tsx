// Competitor comparison for one product, grouped by match status with a coloured
// rail per group. Prices and differences are shown exactly as the backend computed
// them: a difference only when comparison.eligible, never a percentage or saving.
import { useState } from 'react'
import type { CandidateMatch, CompetitorProduct, MatchStatus } from '../api'
import StatusPill from '../components/StatusPill'
import { diffDirection, label, money, plural, signedGbp } from '../format'
import EvidenceDrawer from './EvidencePanel'

const GROUPS: { status: MatchStatus; title: string; note: string }[] = [
  { status: 'EXACT_MATCH', title: 'Exact matches', note: 'Same product identity and required variants.' },
  { status: 'REVIEW_REQUIRED', title: 'Review required', note: 'Possible match with unresolved identity or variant evidence.' },
  { status: 'SIMILAR_MATCH', title: 'Similar alternatives', note: 'Related product or offer, not the same product. Never price-compared.' },
  { status: 'NO_MATCH', title: 'No match', note: 'Evidence does not support a match (for example guides, forums or category pages).' },
]

interface Props {
  candidates: CandidateMatch[]
  pages: Map<string, CompetitorProduct>
  stompboxPrice: string | null
  stompboxCurrency: string
}

export default function ComparisonTable(props: Props) {
  const [open, setOpen] = useState<CandidateMatch | null>(null)
  return (
    <div className="groups">
      {GROUPS.map((g) => {
        const rows = props.candidates.filter((c) => c.match_status === g.status)
        if (rows.length === 0) return null
        const cls = `group group-${g.status.toLowerCase()}`
        const head = (
          <>
            <span className={`group-dot seg-${g.status.toLowerCase()}`} aria-hidden="true" />
            <span className="group-title">
              {g.title} <span className="group-count">{rows.length}</span>
            </span>
            <span className="group-note">{g.note}</span>
          </>
        )
        const table = <GroupTable {...props} rows={rows} onEvidence={setOpen} />
        return g.status === 'NO_MATCH' ? (
          <details key={g.status} className={cls}>
            <summary className="group-head">{head}</summary>
            {table}
          </details>
        ) : (
          <section key={g.status} className={cls} aria-label={`${g.title} (${rows.length})`}>
            <h3 className="group-head">{head}</h3>
            {table}
          </section>
        )
      })}
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

function GroupTable({
  rows,
  pages,
  stompboxPrice,
  stompboxCurrency,
  onEvidence,
}: Props & { rows: CandidateMatch[]; onEvidence: (m: CandidateMatch) => void }) {
  return (
    <div className="table-scroll">
      <table className="data compare">
        <thead>
          <tr>
            <th scope="col">Competitor</th>
            <th scope="col">Page</th>
            <th scope="col">Condition</th>
            <th scope="col" className="num">Competitor price</th>
            <th scope="col" className="num">Difference (competitor − Stompbox)</th>
            <th scope="col">
              <span className="sr-only">Evidence</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => {
            const page = pages.get(m.candidate_id)
            const title = page?.page.title ?? page?.search.title ?? m.product_url
            const compared = m.comparison.eligible && m.comparison.difference_gbp !== null
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
                <td className="wrap">
                  <a href={m.product_url} target="_blank" rel="noreferrer noopener">
                    {title}
                  </a>
                  <span className="cell-sub">
                    <StatusPill value={m.match_status} /> {label(m.match_confidence)} confidence
                  </span>
                </td>
                <td>
                  {label(m.condition.condition)}
                  {!m.condition.comparable_to_new && <span className="cell-sub text-warning">not comparable to new</span>}
                </td>
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
                      {m.comparison.reasons[0] && <span className="cell-sub">{m.comparison.reasons[0]}</span>}
                    </>
                  )}
                </td>
                <td>
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
