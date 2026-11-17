// Stacked bar of candidate counts by match status. Segment widths follow the backend
// counts (flex-grow); labels show counts only, never percentages. A table carries the
// same data for screen readers. "No match" is a neutral hatched segment, not a series.
import type { MatchStatus } from '../api'
import { statusMeta } from '../status'

const ORDER: MatchStatus[] = ['EXACT_MATCH', 'REVIEW_REQUIRED', 'SIMILAR_MATCH', 'NO_MATCH']

interface Props {
  counts: Record<MatchStatus, number>
  caption: string
  compact?: boolean
}

export default function DistributionBar({ counts, caption, compact = false }: Props) {
  const total = ORDER.reduce((sum, s) => sum + (counts[s] ?? 0), 0)
  return (
    <figure className={compact ? 'dist compact' : 'dist'}>
      <div className={total === 0 ? 'dist-bar dist-empty' : 'dist-bar'} aria-hidden="true">
        {ORDER.filter((s) => (counts[s] ?? 0) > 0).map((s) => (
          <span
            key={s}
            className={`dist-seg seg-${s.toLowerCase()}`}
            style={{ flexGrow: counts[s] }}
            title={`${statusMeta(s).label}: ${counts[s]}`}
          />
        ))}
      </div>
      <ul className="dist-legend" aria-hidden="true">
        {ORDER.map((s) => (
          <li key={s} className={counts[s] ? undefined : 'zero'}>
            <span className={`dist-dot seg-${s.toLowerCase()}`} />
            <span className="dist-name">{statusMeta(s).label}</span>
            <span className="dist-count">{counts[s] ?? 0}</span>
          </li>
        ))}
      </ul>
      <table className="sr-only">
        <caption>{caption}</caption>
        <tbody>
          {ORDER.map((s) => (
            <tr key={s}>
              <th scope="row">{statusMeta(s).label}</th>
              <td>{counts[s] ?? 0}</td>
            </tr>
          ))}
          <tr>
            <th scope="row">Total</th>
            <td>{total}</td>
          </tr>
        </tbody>
      </table>
    </figure>
  )
}
