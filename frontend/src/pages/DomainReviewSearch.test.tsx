// Stage 8.1: Domain Review shows why a domain is (not) searched, with no UK evidence UI,
// and keeps benchmark permission as a separate control.
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import App from '../App'
import type { CandidateSummary } from '../api'
import { at, fixture, installEventSource, makeCandidate, mockAppApi } from '../test/fakes'

const RUN_ID = fixture().run.run_id

const classified = (domain: string, over: Partial<CandidateSummary> = {}): CandidateSummary => ({
  ...makeCandidate(domain),
  discovery_mode: 'LLM_ENABLED',
  classification_status: 'COMPLETED',
  classification_version: 3,
  llm_classified: true,
  name_verified: true,
  proposed_company_name: `Shop ${domain}`,
  proposed_company_type: 'retailer',
  serves_uk_customers: 'no',
  serves_uk_customers_note: 'Ships within the EU only',
  ...over,
})

describe('Domain Review search status', () => {
  beforeEach(() => {
    installEventSource()
    localStorage.setItem('stompbox.reviewer', 'Sam')
  })

  const open = async (candidates: CandidateSummary[]) => {
    mockAppApi({ candidates })
    at(`?run=${RUN_ID}`, '#/domains')
    render(<App />)
    await screen.findByRole('tablist', { name: 'Domain sections' })
  }
  const card = async (domain: string) => (await screen.findByRole('link', { name: domain })).closest('li')!

  it('shows automatic, pending and not-yet-classified domains with a reason, and no UK evidence UI', async () => {
    await open([
      classified('auto.example', {
        search_basis: 'AUTOMATIC', search_eligible: true, auto_search_eligible: true,
        search_eligibility_reasons: ['Automatically searchable: retailer named in the discovery evidence (Shop), with cited evidence'],
      }),
      classified('maker.example', { proposed_company_type: 'manufacturer', search_basis: 'NOT_SEARCHED' }),
      makeCandidate('nollm.example'),
    ])
    const auto = await card('auto.example')
    expect(within(auto).getByText('Automatically searchable retailer')).toBeInTheDocument()
    expect(within(auto).getByRole('button', { name: 'Approve…' })).toBeInTheDocument() // still reviewable
    expect(within(await card('maker.example')).getByText('Manufacturer: needs review')).toBeInTheDocument()
    expect(within(await card('nollm.example')).getByText('Pending: not classified yet')).toBeInTheDocument()
    expect(screen.queryByText(/UK evidence/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText('UK relevance')).not.toBeInTheDocument()
  })

  it('shows the reasons and the informational UK answer on demand', async () => {
    const user = userEvent.setup()
    await open([classified('auto.example', {
      search_basis: 'AUTOMATIC', search_eligible: true, auto_search_eligible: true,
      search_eligibility_reasons: ['Automatically searchable: retailer', 'At most 5 automatic domain(s) are searched per run'],
    })])
    const c = await card('auto.example')
    await user.click(within(c).getByRole('button', { name: /Show evidence/ }))
    const reasons = within(c).getByRole('list', { name: 'Search eligibility reasons' })
    expect(within(reasons).getByText('At most 5 automatic domain(s) are searched per run')).toBeInTheDocument()
    expect(within(c).getByText(/does not sell to UK customers: Ships within the EU only/)).toHaveTextContent(
      /never decides whether the domain is searched/,
    )
  })

  it('keeps benchmark permission separate: automatic search never ticks it', async () => {
    const user = userEvent.setup()
    await open([classified('auto.example', { search_basis: 'AUTOMATIC', search_eligible: true, auto_search_eligible: true })])
    const c = await card('auto.example')
    await user.click(within(c).getByRole('button', { name: 'Approve…' }))
    await user.selectOptions(within(c).getByLabelText(/Company type/), 'retailer')
    const box = within(c).getByRole('checkbox', { name: /Use as a price benchmark/ })
    expect(box).not.toBeChecked()
    expect(within(c).getByText(/never makes a domain a price/)).toBeInTheDocument()
    expect(within(c).queryByText(/UK is not served|verified UK evidence/)).not.toBeInTheDocument()
  })

  it('shows a used-only retailer as needing review, with approval and an unticked benchmark box', async () => {
    const user = userEvent.setup()
    await open([classified('used.example', { search_basis: 'NOT_SEARCHED', sells_new_musical_instruments: false,
      search_eligibility_reasons: ['Not searched until approved: the evidence shows it sells only used instruments'] })])
    const c = await card('used.example')
    expect(within(c).getByText('Used-only retailer: needs review')).toBeInTheDocument()
    await user.click(within(c).getByRole('button', { name: 'Approve…' }))
    await user.selectOptions(within(c).getByLabelText(/Company type/), 'specialist_retailer')
    expect(within(c).getByRole('checkbox', { name: /Use as a price benchmark/ })).not.toBeChecked()
  })

  it('shows rejected and manually approved domains distinctly', async () => {
    await open([
      classified('approved.example', { review_status: 'APPROVED', search_basis: 'MANUAL_APPROVAL', decision: makeCandidate('approved.example', 'APPROVED').decision }),
      classified('gone.example', { review_status: 'REJECTED', search_basis: 'NOT_SEARCHED', decision: makeCandidate('gone.example', 'REJECTED').decision }),
    ])
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: /Approved/ }))
    expect(within(await card('approved.example')).getByText('Manually approved: searched')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: /Rejected/ }))
    expect(within(await card('gone.example')).getByText('Rejected: never searched')).toBeInTheDocument()
  })
})
