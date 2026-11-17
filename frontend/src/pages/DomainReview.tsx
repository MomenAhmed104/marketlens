// Competitor domains: reviewer decisions (Phase 4 workflow, unchanged), configured
// competitors, and domains excluded or skipped. Discovered domains show a competitor
// classification (Direct Competitor / Related Retailer / Needs Review, see competitorClass.ts).
import { useEffect, useState } from 'react'
import { fetchCompetitors, type CompetitorDomain } from '../api'
import { useRunContext } from '../app/runContext'
import Avatar from '../components/Avatar'
import EmptyState from '../components/EmptyState'
import Tabs, { TabPanel } from '../components/Tabs'
import RunDomains from '../dashboard/RunDomains'
import { label, plural } from '../format'
import Review from '../Review'

type Section = 'PENDING' | 'APPROVED' | 'REJECTED' | 'configured' | 'excluded'
const LABELS: Record<Section, string> = {
  PENDING: 'Pending',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
  configured: 'Configured',
  excluded: 'Excluded and skipped',
}
const ORDER: Section[] = ['PENDING', 'APPROVED', 'REJECTED', 'configured', 'excluded']

export default function DomainReview() {
  const { candidates, reviewChanged, intelCfg, view } = useRunContext()
  const [section, setSection] = useState<Section>('PENDING')
  const [competitors, setCompetitors] = useState<CompetitorDomain[] | null>(null)
  const [compError, setCompError] = useState<string | null>(null)

  useEffect(() => {
    fetchCompetitors().then(setCompetitors, (e: Error) => setCompError(e.message))
  }, [])

  const excluded = view.result?.discovery?.excluded ?? []
  const skipped = intelCfg?.skipped_targets ?? []
  const count = (s: Section): number | null => {
    if (s === 'configured') return competitors?.length ?? null
    if (s === 'excluded') return excluded.length + skipped.length
    return candidates ? candidates.filter((c) => c.review_status === s).length : null
  }

  return (
    <div className="stack">
      {view.result && (
        <section className="panel-flat" aria-labelledby="this-run-heading">
          <div className="section-head">
            <h2 id="this-run-heading">Selected run</h2>
            <span className="hint-inline">What this run searched and discovered</span>
          </div>
          <RunDomains result={view.result} onShowPending={() => setSection('PENDING')} />
        </section>
      )}

      <section aria-labelledby="domains-heading" className="stack-sm">
        <div className="section-head">
          <h2 id="domains-heading">Competitor domains</h2>
          <span className="hint-inline">Approvals apply to the next run. Pending and rejected domains are never searched.</span>
        </div>
        <Tabs
          label="Domain sections"
          idPrefix="domains"
          value={section}
          onChange={setSection}
          tabs={ORDER.map((id) => ({ id, label: LABELS[id], count: count(id) }))}
        />
        <TabPanel idPrefix="domains" id={section}>
          {(section === 'PENDING' || section === 'APPROVED' || section === 'REJECTED') && (
            <Review key={section} status={section} onChanged={reviewChanged} />
          )}
          {section === 'configured' && <Configured competitors={competitors} error={compError} />}
          {section === 'excluded' && <Excluded excluded={excluded} skipped={skipped} runId={view.run?.run_id ?? null} />}
        </TabPanel>
      </section>
    </div>
  )
}

function Configured({ competitors, error }: { competitors: CompetitorDomain[] | null; error: string | null }) {
  if (error) return <p role="alert" className="callout danger">Could not load configured competitors: {error}</p>
  if (!competitors) return <p className="hint">Loading configured competitors…</p>
  return (
    <>
      <p className="hint">
        The {competitors.length} competitors from the catalogue workbook. They are always searched and are not reviewed
        here.
      </p>
      <ul className="tile-grid">
        {competitors.map((c) => (
          <li key={c.domain} className="tile">
            <div className="tile-head">
              <Avatar name={c.name} />
              <div className="domain-id">
                <span className="domain-name">{c.name}</span>
                <a href={c.homepage_url} target="_blank" rel="noreferrer noopener" className="cell-mono">
                  {c.domain}
                </a>
              </div>
            </div>
            <p className="tile-meta">
              {label(c.company_type)}
              {c.priority && ` · ${c.priority} priority`}
            </p>
            <span className={c.price_benchmark ? 'elig yes' : 'elig no'}>
              <span aria-hidden="true">{c.price_benchmark ? '★' : '–'}</span>
              {c.price_benchmark ? 'Benchmark' : 'Reference only, not a benchmark'}
            </span>
          </li>
        ))}
      </ul>
    </>
  )
}

function Excluded(props: {
  excluded: { domain: string; reason: string; evidence_count: number }[]
  skipped: { domain: string; company_type: string | null; reason: string }[]
  runId: string | null
}) {
  const { excluded, skipped, runId } = props
  if (excluded.length + skipped.length === 0) {
    return <EmptyState title="Nothing excluded or skipped" />
  }
  return (
    <div className="stack-sm">
      {skipped.length > 0 && (
        <div>
          <h3 className="sub-head">Approved but not searched ({skipped.length})</h3>
          <p className="hint">Reviewer-approved domains of a type that is never searched (for example directories).</p>
          <ul className="reason-list">
            {skipped.map((s) => (
              <li key={s.domain}>
                <span className="cell-mono">{s.domain}</span>
                {s.company_type && <span className="tag">{label(s.company_type)}</span>}
                <span>{s.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {excluded.length > 0 && (
        <div>
          <h3 className="sub-head">Excluded by discovery rules in the selected run ({excluded.length})</h3>
          <p className="hint">From run {runId}. These are not stored: they exist only in that run's results.</p>
          <ul className="reason-list">
            {excluded.map((x) => (
              <li key={x.domain}>
                <span className="cell-mono">{x.domain}</span>
                <span>{x.reason}</span>
                <span className="hint-inline">{plural(x.evidence_count, 'evidence item')}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
