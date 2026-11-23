import { render, screen } from '@testing-library/react'
import { createElement } from 'react'
import { describe, expect, it } from 'vitest'
import type { DiscoveredCompetitor, DomainDecision, DomainRunOutcome, PipelineResult } from '../api'
import { domainOutcomes, eligibleNotSearched, runDomainStatus, searchedDomains } from '../domainStatus'
import { DiscoveredDomainsTable } from './RunDomains'
import { fixture } from '../test/fakes'

const decision = (d: 'APPROVED' | 'REJECTED') => ({ decision: d }) as DomainDecision
const c = (approval_status: DiscoveredCompetitor['approval_status'], review_decision: DomainDecision | null = null,
  domain = 'shop.example') => ({ domain, approval_status, review_decision })
const searched = new Set(['shop.example'])

describe('runDomainStatus', () => {
  it('uses the run search records, not approval alone', () => {
    expect(runDomainStatus(c('APPROVED', decision('APPROVED')), searched)).toBe('Searched in this run')
    expect(runDomainStatus(c('AUTO_APPROVED'), searched)).toBe('Searched in this run')
    expect(runDomainStatus(c('APPROVED', decision('APPROVED')))).toBe('Approved — not searched in this run')
    expect(runDomainStatus(c('AUTO_APPROVED'))).toBe('Automatically eligible — not searched in this run')
    expect(runDomainStatus(c('PRECONFIGURED'), searched)).toBe('Configured competitor')
    expect(runDomainStatus(c('APPROVAL_REQUIRED'))).toBe('Pending review')
  })

  it('a rejection always wins, even over a search record', () => {
    expect(runDomainStatus(c('REJECTED', decision('REJECTED')), searched)).toBe('Rejected — never searched')
    expect(runDomainStatus(c('AUTO_APPROVED', decision('REJECTED')), searched)).toBe('Rejected — never searched')
    expect(runDomainStatus(c('REJECTED'))).toBe('Rejected — never searched') // automatic: no discovery evidence
  })
})

describe('searchedDomains', () => {
  it('collects only domains a product search actually queried', () => {
    const r = {
      products: [
        { intelligence: { domains: [{ domain: 'a.example', queries: ['q'], results: 0, pages_selected: 0, errors: [] },
                                    { domain: 'b.example', queries: [], results: 0, pages_selected: 0, errors: [] }] } },
        { intelligence: null },
      ],
    } as unknown as PipelineResult
    expect([...searchedDomains(r)]).toEqual(['a.example'])
    expect(searchedDomains(null).size).toBe(0)
  })

  it('matches the dashboard fixture run', () => {
    const f = fixture()
    const s = searchedDomains(f.result)
    expect(s.size).toBeGreaterThan(0)
    for (const d of s) expect(f.result.search_targets.map((t) => t.domain)).toContain(d)
  })
})

describe('domain outcomes (Stage 8.2)', () => {
  const outcome = (domain: string, status: DomainRunOutcome['status'], reason: string | null = null) => ({ domain, status, reason })

  it('lists approved and automatically eligible domains that were not targets, with the backend reason', () => {
    const r = {
      domain_outcomes: [
        outcome('a.example', 'SELECTED_FOR_SEARCH'),
        outcome('b.example', 'AUTO_ELIGIBLE_NOT_SEARCHED', 'Automatic search is switched off (PIPELINE_SEARCH_AUTO_APPROVED=false)'),
        outcome('c.example', 'APPROVED_NOT_SEARCHED', 'Outside the domains selected for this run'),
        outcome('d.example', 'PENDING_APPROVAL', 'Needs a reviewer decision'),
        outcome('e.example', 'REJECTED', 'Rejected in review'),
      ],
    } as unknown as PipelineResult
    expect(eligibleNotSearched(r).map((o) => o.domain)).toEqual(['b.example', 'c.example'])
    expect(domainOutcomes(r).get('e.example')?.status).toBe('REJECTED')
  })

  it('runs saved before Stage 8.2 have no outcomes and fall back safely', () => {
    const old = { products: [] } as unknown as PipelineResult
    expect(eligibleNotSearched(old)).toEqual([])
    expect(domainOutcomes(old).size).toBe(0)
    expect(domainOutcomes(null).size).toBe(0)
  })

  it('shows the not-searched reason in the discovered-domains table, never for searched domains', () => {
    const cands = [
      { domain: 'b.example', approval_status: 'AUTO_APPROVED', review_decision: null, homepage_url: 'https://b.example/',
        company_type: 'retailer', competitor_class: null, classification_status: 'COMPLETED', decision_reasons: [] },
      { domain: 'a.example', approval_status: 'AUTO_APPROVED', review_decision: null, homepage_url: 'https://a.example/',
        company_type: 'retailer', competitor_class: null, classification_status: 'COMPLETED', decision_reasons: [] },
    ] as unknown as DiscoveredCompetitor[]
    const outcomes = new Map([
      ['b.example', outcome('b.example', 'AUTO_ELIGIBLE_NOT_SEARCHED', 'Automatic search is switched off (PIPELINE_SEARCH_AUTO_APPROVED=false)')],
      ['a.example', outcome('a.example', 'SELECTED_FOR_SEARCH')],
    ])
    render(createElement(DiscoveredDomainsTable, { candidates: cands, searched: new Set(['a.example']), outcomes }))
    const statuses = screen.getAllByTestId('run-domain-status').map((e) => e.textContent)
    expect(statuses).toEqual(['Automatically eligible — not searched in this run', 'Searched in this run'])
    const reasons = screen.getAllByTestId('run-domain-reason')
    expect(reasons).toHaveLength(1)
    expect(reasons[0]).toHaveTextContent('Automatic search is switched off in the server settings')
  })
})
