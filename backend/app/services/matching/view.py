"""Task C: the evidence-backed Product Intelligence output, assembled in Python.

One `ProductIntelligenceView` per candidate answers the four questions of the
Product Intelligence Agent (implementation plan §3.6) from already-validated
data only. Nothing is inferred here: missing values stay missing, and every
uncertainty found along the way is listed explicitly. No LLM call is made.
"""

from __future__ import annotations

from app.agents.intelligence.schemas import CompetitorProduct, FieldStatus, ProposalStatus
from app.schemas.common import AvailabilityStatus, MatchStatus
from app.services.matching.schemas import (
    AvailabilityView,
    CandidateMatch,
    MatchView,
    PriceKind,
    PriceStatus,
    PriceView,
    ProductIntelligenceView,
    ProposalAgreement,
    SellerView,
)

_FIELD_UNCERTAIN = {FieldStatus.UNSUPPORTED, FieldStatus.AMBIGUOUS, FieldStatus.CONFLICT, FieldStatus.INVALID}
_PRICE_UNCERTAIN = {PriceStatus.AMBIGUOUS, PriceStatus.CONFLICT, PriceStatus.UNSUPPORTED, PriceStatus.INVALID,
                    PriceStatus.WRONG_CURRENCY, PriceStatus.EXCLUDED_SALE_PRICE}  # fmt: skip


def _uncertainties(m: CandidateMatch, c: CompetitorProduct, availability: AvailabilityView) -> list[str]:
    out: list[str] = []
    for e in c.field_evidence:
        if e.status in _FIELD_UNCERTAIN:
            out.append(f"{e.field.value}: {e.status.value}" + (f" ({e.note})" if e.note else ""))
    for i in c.extraction_issues:
        if i.verified:
            out.append(f"LLM 2 flagged {i.kind.value} on {', '.join(i.fields) or 'the page'}: {i.description}")
    if m.price.status in _PRICE_UNCERTAIN:
        out += [f"price: {r}" for r in m.price.reasons]
    elif m.price.status is PriceStatus.MISSING:
        out.append("price: no current price found on the page (missing, not zero)")
    if availability.status is AvailabilityStatus.UNKNOWN and availability.reason:
        out.append(f"availability: unknown ({availability.reason})")
    out += [f"identity: {r}" for r in m.review_reasons]
    out += [f"specification: {r}" for r in m.spec_category_mismatches]
    if c.match_proposal_status is ProposalStatus.UNSUPPORTED:
        out.append("LLM 2 match proposal had no verified quote and was ignored")
    elif c.match_proposal_status is ProposalStatus.FAILED:
        out.append("LLM 2 match proposal failed; the final status rests on Python's checks only")
    if m.proposal_agreement is ProposalAgreement.PYTHON_STRICTER:
        out.append(f"LLM 2 proposed {m.proposed_match.value}; Python's checks gave the more cautious "
                   f"{m.match_status.value}")  # fmt: skip
    elif m.proposal_agreement is ProposalAgreement.LLM_STRICTER and m.match_status is not MatchStatus.REVIEW_REQUIRED:
        out.append(f"LLM 2 proposed the more cautious {m.proposed_match.value}; recorded only")
    return list(dict.fromkeys(out))


def build_view(m: CandidateMatch, c: CompetitorProduct) -> ProductIntelligenceView:
    t = m.competitor
    alt = m.alternative_price
    not_shown: list[str] = []
    if m.price_kind is PriceKind.NONE:
        not_shown = list(m.comparison.reasons) if m.match_status is MatchStatus.EXACT_MATCH else (
            list(alt.reasons) if alt else [f"Match status is {m.match_status.value}: price not shown"])  # fmt: skip
    shown = m.price_kind is not PriceKind.NONE or m.match_status is MatchStatus.EXACT_MATCH
    price = PriceView(
        kind=m.price_kind, status=m.price.status, evidence_status=m.price.evidence_status,
        price=m.price.price if shown else None, currency=m.price.currency if shown else None,
        price_text=m.price.price_text, quote=m.price.quote, source_url=m.price.source_url,
        retrieved_at=m.price.retrieved_at,
        difference_gbp=m.comparison.difference_gbp if m.price_kind is PriceKind.DIRECT else None,
        condition_label=alt.condition_label if alt else None, not_shown_reasons=not_shown,
    )  # fmt: skip
    availability = m.availability or AvailabilityView(status=AvailabilityStatus.UNKNOWN)
    return ProductIntelligenceView(
        candidate_id=m.candidate_id, product_url=m.product_url,
        seller=SellerView(domain=t.domain, company_name=t.company_name, company_type=t.company_type,
                          approval_status=t.approval_status, price_benchmark=t.price_benchmark),
        match=MatchView(status=m.match_status, confidence=m.match_confidence, proposed=m.proposed_match,
                        proposal_status=m.proposal_status, agreement=m.proposal_agreement,
                        reasons=m.match_reasons, review_reasons=m.review_reasons),
        price=price, availability=availability, uncertainties=_uncertainties(m, c, availability),
    )  # fmt: skip
