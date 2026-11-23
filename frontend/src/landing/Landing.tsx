// Marketing landing page (Stage 8.2). Static and self-contained: no backend call, no data of its
// own. The product images are real screenshots of this dashboard showing a recorded run, and are
// labelled as such. Styles are scoped to `.lp` in landing.css, so the dashboard is unaffected.
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { DASHBOARD_HREF } from '../app/router'
import { BrandMark } from '../components/icons'
import {
  ArrowRightIcon,
  ChartIcon,
  CheckCheckIcon,
  ClockIcon,
  FileSearchIcon,
  LayersIcon,
  PoundIcon,
  RadarIcon,
  ScanSearchIcon,
  ShieldAlertIcon,
  ShuffleIcon,
  UploadIcon,
} from './icons'
import './landing.css'

const SECTIONS = [
  { id: 'lp-home', label: 'Home' },
  { id: 'lp-platform', label: 'Platform' },
  { id: 'lp-how', label: 'How It Works' },
  { id: 'lp-capabilities', label: 'Capabilities' },
] as const

const COPYRIGHT_YEAR = new Date().getFullYear()
const RUN_NOTE = 'Recorded run, 20 Nov 2026: two catalogue products searched on Andertons and Gear4music'

const PROBLEMS = [
  { Icon: LayersIcon, title: 'Fragmented competitor information', text: 'Prices, stock and product details sit on dozens of retailer sites, each organised differently.' },
  { Icon: ClockIcon, title: 'Slow manual comparisons', text: 'Checking one product across several shops by hand takes time, and the answers go out of date.' },
  { Icon: ShuffleIcon, title: 'Products that only look the same', text: 'A different size, colour, variant or bundle is easy to miss, and comparing it as the same product misleads.' },
  { Icon: ShieldAlertIcon, title: 'Prices you cannot verify', text: 'A search snippet or a crossed-out “was” price is not the current selling price.' },
]

const CAPABILITIES = [
  { Icon: RadarIcon, title: 'Competitor Discovery', text: 'Discover relevant retailers through AI-powered web research, then keep the final say on which ones are searched.' },
  { Icon: ScanSearchIcon, title: 'Product Intelligence', text: 'Identify exact product matches and distinguish them from equivalent variants and similar alternatives.' },
  { Icon: PoundIcon, title: 'Price Intelligence', text: 'Compare extracted competitor prices and availability with your own price, product by product.' },
  { Icon: FileSearchIcon, title: 'Evidence-Based Insights', text: 'Inspect the source page, the extracted value and the quote that supports it behind every result.' },
]

const STEPS = [
  { Icon: UploadIcon, title: 'Start from your product catalogue', text: 'Your own products, loaded from your catalogue file: brand, model, variant and your current price.' },
  { Icon: RadarIcon, title: 'Discover relevant competitors', text: 'Known competitors are reused and new retailers found; retailers that pass basic checks can be searched, and you approve or reject any domain.' },
  { Icon: CheckCheckIcon, title: 'Find and validate product matches', text: 'Competitor pages are read, then matched and price-checked by deterministic rules.' },
  { Icon: ChartIcon, title: 'Explore competitive intelligence', text: 'See matches, prices, availability and the evidence for each, live as a run progresses.' },
]

const SHOWCASE = [
  { id: 'overview', label: 'Overview', src: '/landing/overview.webp', text: 'The headline result of a run: exact matches, eligible price comparisons and competitor coverage.' },
  { id: 'products', label: 'Product Intelligence', src: '/landing/products.webp', text: 'One product against every competitor page found, grouped by how well it matches.' },
  { id: 'evidence', label: 'Evidence drawer', src: '/landing/evidence.webp', text: 'The verified price, its verbatim page text, the source link and how the difference was worked out.' },
  { id: 'domains', label: 'Domain Review', src: '/landing/domains.webp', text: 'Discovered retailers, why each is or is not searched, and the approve or reject decision.' },
  { id: 'runs', label: 'Live Runs', src: '/landing/runs.webp', text: 'Each run’s pipeline, stage by stage, with its outcome and usage.' },
] as const

function scrollToSection(id: string) {
  const el = document.getElementById(id)
  if (!el) return
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
}

/** Fades content in as it scrolls into view; everything is visible without IntersectionObserver. */
function Reveal({ children, className = '', delay = 0 }: { children: ReactNode; className?: string; delay?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const [shown, setShown] = useState(typeof IntersectionObserver === 'undefined')
  useEffect(() => {
    if (shown || !ref.current) return
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setShown(true)
          io.disconnect()
        }
      },
      { rootMargin: '0px 0px -8% 0px' },
    )
    io.observe(ref.current)
    // Safety net: content is never left hidden if the observer does not report (e.g. a print
    // or screenshot renderer). The reveal only ever delays what is shown, never hides it.
    const t = window.setTimeout(() => setShown(true), 1600)
    return () => {
      io.disconnect()
      window.clearTimeout(t)
    }
  }, [shown])
  return (
    <div ref={ref} className={`lp-reveal ${shown ? 'is-shown' : ''} ${className}`.trim()} style={{ transitionDelay: `${delay}ms` }}>
      {children}
    </div>
  )
}

function Screenshot({ src, alt, eager = false }: { src: string; alt: string; eager?: boolean }) {
  return (
    <figure className="lp-shot">
      <div className="lp-shot-bar" aria-hidden="true">
        <span /> <span /> <span />
      </div>
      <img src={src} alt={alt} width={1440} height={900} loading={eager ? 'eager' : 'lazy'} decoding="async" />
    </figure>
  )
}

export default function Landing() {
  const [shot, setShot] = useState<(typeof SHOWCASE)[number]['id']>('overview')
  const [scrolled, setScrolled] = useState(false)
  const current = SHOWCASE.find((s) => s.id === shot)!

  useEffect(() => {
    document.title = 'MarketLens · Competitor and price intelligence'
    const onScroll = () => setScrolled(window.scrollY > 8)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      window.removeEventListener('scroll', onScroll)
      document.title = 'MarketLens'
    }
  }, [])

  return (
    <div className="lp">
      <a className="lp-skip" href="#lp-main" onClick={(e) => { e.preventDefault(); scrollToSection('lp-main') }}>
        Skip to content
      </a>
      <header className={scrolled ? 'lp-nav is-scrolled' : 'lp-nav'}>
        <div className="lp-container lp-nav-inner">
          <button type="button" className="lp-brand" onClick={() => scrollToSection('lp-home')}>
            <BrandMark />
            <span>Market<strong>Lens</strong></span>
          </button>
          <nav aria-label="Landing page" className="lp-nav-links">
            {SECTIONS.map((s) => (
              <button key={s.id} type="button" className="lp-nav-link" onClick={() => scrollToSection(s.id)}>
                {s.label}
              </button>
            ))}
          </nav>
          <a className="lp-btn lp-btn-primary lp-btn-sm" href={DASHBOARD_HREF}>
            Explore Dashboard
          </a>
        </div>
      </header>

      <main id="lp-main" tabIndex={-1}>
        <section id="lp-home" className="lp-hero" aria-labelledby="lp-hero-title">
          <div className="lp-hero-glow" aria-hidden="true" />
          <div className="lp-container lp-hero-grid">
            <Reveal className="lp-hero-copy">
              <p className="lp-kicker">Your competitors. Your products. One intelligence platform.</p>
              <h1 id="lp-hero-title" className="lp-h1">
                Know Your Market. <span className="lp-gradient-text">Price With Confidence.</span>
              </h1>
              <p className="lp-lead">
                Discover competing retailers, identify matching products, compare market prices and explore the evidence
                behind every insight.
              </p>
              <div className="lp-actions">
                <a className="lp-btn lp-btn-primary" href={DASHBOARD_HREF}>
                  Explore Intelligence Dashboard <ArrowRightIcon />
                </a>
                <button type="button" className="lp-btn lp-btn-ghost" onClick={() => scrollToSection('lp-how')}>
                  Discover How It Works
                </button>
              </div>
            </Reveal>
            <Reveal className="lp-hero-visual" delay={120}>
              <Screenshot src="/landing/overview.webp" alt="Screenshot of the dashboard Overview for a recorded run" eager />
              <p className="lp-caption">Dashboard screenshot · {RUN_NOTE}</p>
            </Reveal>
          </div>
        </section>

        <section id="lp-problem" className="lp-section" aria-labelledby="lp-problem-title">
          <div className="lp-container">
            <Reveal className="lp-section-head">
              <p className="lp-eyebrow">The problem</p>
              <h2 id="lp-problem-title" className="lp-h2">Competitor pricing is hard to see clearly</h2>
            </Reveal>
            <div className="lp-problems">
              {PROBLEMS.map(({ Icon, title, text }, i) => (
                <Reveal key={title} className="lp-problem" delay={i * 70}>
                  <span className="lp-icon lp-icon-muted"><Icon /></span>
                  <h3>{title}</h3>
                  <p>{text}</p>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        <section id="lp-capabilities" className="lp-section lp-section-alt" aria-labelledby="lp-cap-title">
          <div className="lp-container">
            <Reveal className="lp-section-head">
              <p className="lp-eyebrow">Capabilities</p>
              <h2 id="lp-cap-title" className="lp-h2">From discovery to a decision you can defend</h2>
            </Reveal>
            <div className="lp-caps">
              {CAPABILITIES.map(({ Icon, title, text }, i) => (
                <Reveal key={title} className="lp-cap" delay={i * 70}>
                  <span className="lp-icon"><Icon /></span>
                  <div>
                    <h3>{title}</h3>
                    <p>{text}</p>
                  </div>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        <section id="lp-how" className="lp-section" aria-labelledby="lp-how-title">
          <div className="lp-container">
            <Reveal className="lp-section-head">
              <p className="lp-eyebrow">How it works</p>
              <h2 id="lp-how-title" className="lp-h2">Four steps from catalogue to insight</h2>
            </Reveal>
            <ol className="lp-steps">
              {STEPS.map(({ Icon, title, text }, i) => (
                <li key={title}>
                  <Reveal className="lp-step" delay={i * 90}>
                    <span className="lp-step-icon"><Icon /></span>
                    <span className="lp-step-num">Step {i + 1}</span>
                    <h3>{title}</h3>
                    <p>{text}</p>
                  </Reveal>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="lp-platform" className="lp-section lp-section-alt" aria-labelledby="lp-show-title">
          <div className="lp-container">
            <Reveal className="lp-section-head">
              <p className="lp-eyebrow">The platform</p>
              <h2 id="lp-show-title" className="lp-h2">See it in the dashboard</h2>
              <p className="lp-sub">Real screenshots of the application. {RUN_NOTE}.</p>
            </Reveal>
            <div className="lp-showcase">
              <div className="lp-tabs" role="tablist" aria-label="Dashboard views">
                {SHOWCASE.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    role="tab"
                    id={`lp-tab-${s.id}`}
                    aria-selected={s.id === shot}
                    aria-controls="lp-showcase-panel"
                    className="lp-tab"
                    onClick={() => setShot(s.id)}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
              <div id="lp-showcase-panel" role="tabpanel" aria-labelledby={`lp-tab-${shot}`} className="lp-showcase-panel">
                <p className="lp-showcase-text">{current.text}</p>
                <Screenshot key={current.id} src={current.src} alt={`Screenshot of the dashboard: ${current.label}`} />
              </div>
            </div>
          </div>
        </section>

        <section className="lp-cta" aria-labelledby="lp-cta-title">
          <div className="lp-container">
            <Reveal className="lp-cta-card">
              <h2 id="lp-cta-title" className="lp-h2">Turn Competitor Data Into Better Pricing Decisions.</h2>
              <a className="lp-btn lp-btn-primary" href={DASHBOARD_HREF}>
                Explore Intelligence Dashboard <ArrowRightIcon />
              </a>
            </Reveal>
          </div>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-container lp-footer-inner">
          <div className="lp-footer-brand">
            <span className="lp-brand-static">
              <BrandMark />
              <span>Market<strong>Lens</strong></span>
            </span>
            <p>Competitor discovery, product matching and evidence-backed price intelligence for retailers. Demonstrated on a musical-instrument retailer’s catalogue.</p>
          </div>
          <nav aria-label="Footer" className="lp-footer-links">
            {SECTIONS.map((s) => (
              <button key={s.id} type="button" className="lp-nav-link" onClick={() => scrollToSection(s.id)}>
                {s.label}
              </button>
            ))}
            <a className="lp-nav-link" href={DASHBOARD_HREF}>Dashboard</a>
          </nav>
        </div>
        <div className="lp-container lp-copy">© {COPYRIGHT_YEAR} MarketLens.</div>
      </footer>
    </div>
  )
}
