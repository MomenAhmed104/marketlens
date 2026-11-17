import { COMPETITOR_CLASS_LABEL, type ClassSource, type CompetitorClass } from '../competitorClass'

const CLASS_ICON: Record<CompetitorClass, string> = { DIRECT: '✓', RELATED: '≈', NEEDS_REVIEW: '?' }

const FALLBACK_TITLE = 'Not yet classified by the backend (older record): provisional class'

/** Competitor classification pill (Direct Competitor / Related Retailer / Needs Review). */
export default function CompetitorClassPill({ cls, source = 'backend' }: { cls: CompetitorClass; source?: ClassSource }) {
  return (
    <span
      className={`pill class-${cls.toLowerCase().replace('_', '-')}`}
      data-testid="competitor-class"
      data-source={source}
      title={source === 'fallback' ? FALLBACK_TITLE : undefined}
    >
      <span className="pill-icon" aria-hidden="true">{CLASS_ICON[cls]}</span>
      {COMPETITOR_CLASS_LABEL[cls]}
    </span>
  )
}
