import type { ComponentType } from 'react'
import type { Health } from '../api'
import { BrandMark, DomainsIcon, OverviewIcon, ProductIcon, RunsIcon } from '../components/icons'
import { plural } from '../format'
import { HOME_HREF, href, PAGE_TITLES, type Page } from './router'
import { useRunContext } from './runContext'

const GROUPS: { label: string; items: { page: Page; Icon: ComponentType }[] }[] = [
  {
    label: 'Analyse',
    items: [
      { page: 'overview', Icon: OverviewIcon },
      { page: 'products', Icon: ProductIcon },
    ],
  },
  {
    label: 'Operate',
    items: [
      { page: 'runs', Icon: RunsIcon },
      { page: 'domains', Icon: DomainsIcon },
    ],
  },
]

interface Props {
  current: Page
  health: Health
  open: boolean
  onNavigate: () => void
}

export default function Sidebar({ current, health, open, onNavigate }: Props) {
  const { candidates, discoveryCfg, intelCfg, active } = useRunContext()
  const pending = candidates?.filter((c) => c.review_status === 'PENDING').length ?? 0
  const provider = intelCfg?.search_provider ?? discoveryCfg?.search_provider ?? null
  const llm1 = discoveryCfg ? (discoveryCfg.mode === 'LLM_ENABLED' ? discoveryCfg.llm1_model : null) : undefined
  const llm2 = intelCfg ? (intelCfg.mode === 'LLM_ENABLED' ? intelCfg.llm2_model : null) : undefined

  return (
    <aside id="sidebar" className={open ? 'sidebar open' : 'sidebar'} aria-label="Main">
      <a className="brand brand-link" href={HOME_HREF} title="Back to the MarketLens home page">
        <BrandMark />
        <span className="brand-text nav-label">
          MarketLens
          <span>Competitor Intelligence</span>
        </span>
      </a>
      <a className="home-link nav-label" href={HOME_HREF}>
        <span aria-hidden="true">←</span> Back to home
      </a>
      <nav aria-label="Sections" className="nav">
        {GROUPS.map((g) => (
          <div key={g.label} className="nav-group">
            <p className="nav-group-label nav-label">{g.label}</p>
            <ul className="nav-list">
              {g.items.map(({ page, Icon }) => {
                const label = PAGE_TITLES[page]
                const running = page === 'runs' && active
                const count = page === 'domains' ? pending : 0
                const name = `${label}${running ? ', run in progress' : ''}${count ? `, ${count} pending review` : ''}`
                return (
                  <li key={page}>
                    <a
                      href={href(page)}
                      className="nav-item"
                      aria-current={current === page ? 'page' : undefined}
                      aria-label={name}
                      title={name}
                      onClick={onNavigate}
                    >
                      <span className="nav-icon">
                        <Icon />
                      </span>
                      <span className="nav-label">{label}</span>
                      {running && (
                        <span className="nav-live" aria-hidden="true">
                          <span className="nav-live-dot" />
                          <span className="nav-label">Live</span>
                        </span>
                      )}
                      {count > 0 && (
                        <span className="nav-count" aria-hidden="true">
                          {count}
                        </span>
                      )}
                    </a>
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </nav>

      <section className="system nav-label" aria-label="Configuration">
        <p className="nav-group-label">System</p>
        <dl>
          <SystemRow label="Search" value={provider} />
          <SystemRow label="LLM 1 discovery" value={llm1} />
          <SystemRow label="LLM 2 extraction" value={llm2} />
          <div className="sys-row">
            <dt>
              <span className={health.status === 'ok' ? 'sys-dot on' : 'sys-dot'} aria-hidden="true" />
              Backend
            </dt>
            <dd>{health.status}</dd>
          </div>
        </dl>
        <p className="sys-note">
          {plural(health.products, 'product')} · {plural(health.competitors, 'competitor')}
        </p>
      </section>
    </aside>
  )
}

/** value: string = on (model or provider name), null = off, undefined = still loading. */
function SystemRow({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="sys-row">
      <dt>
        <span className={value ? 'sys-dot on' : 'sys-dot'} aria-hidden="true" />
        {label}
      </dt>
      <dd>{value === undefined ? '…' : (value ?? 'off')}</dd>
    </div>
  )
}
