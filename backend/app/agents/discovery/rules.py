"""Deterministic discovery rules: evidence signals, validation and approval.

LLM 1 proposes company names, types, relevance and confidence. Everything in
this module is plain Python, so every decision is reproducible and explainable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.agents.discovery.schemas import (
    ClassificationStatus,
    CompanyClassification,
    CompetitorClass,
    DiscoveredCompetitor,
    EvidenceItem,
    EvidenceKind,
    UkSeller,
)
from app.schemas.common import DomainApprovalStatus
from app.schemas.competitor import BENCHMARK_ELIGIBLE_TYPES, CompanyType
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.services.text_match import gbp_prices

RATIONALE_MAX_CHARS = 600

# --------------------------------------------------------------------------- UK signals

_UK_POSTCODE = re.compile(r"\b(?:[A-Z]{1,2}\d[A-Z\d]?|GIR) ?\d[A-Z]{2}\b")
_UK_WORDS = re.compile(r"\b(?:UK|United Kingdom|England|Scotland|Wales|Northern Ireland|Great Britain)\b")
# Strong signals can support auto-approval; "uk_mention" alone cannot.
STRONG_UK_SIGNALS = {"uk_domain", "gbp_price", "uk_postcode"}


def uk_signals(domain: str, evidence: list[EvidenceItem]) -> list[str]:
    signals = []
    if domain.endswith(".uk"):
        signals.append("uk_domain")
    text = " ".join(f"{e.title or ''} {e.snippet or ''}" for e in evidence)
    if gbp_prices(text):
        signals.append("gbp_price")
    if _UK_POSTCODE.search(text):
        signals.append("uk_postcode")
    if _UK_WORDS.search(text):
        signals.append("uk_mention")
    return signals


# --------------------------------------------------------------------------- name verification


def _compact(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", text.casefold().replace("&", "and"))


def name_in_evidence(name: str, evidence: list[EvidenceItem]) -> bool:
    """True if the company name is written in a title or snippet from this domain.

    The hostname alone never verifies a name: a similar-looking domain is not
    evidence that a company owns it.
    """
    key = _compact(name)
    if len(key) < 3:
        return False
    return any(key in _compact(f"{e.title or ''} {e.snippet or ''}") for e in evidence)


# --------------------------------------------------------------------------- applying LLM output


@dataclass(frozen=True)
class Thresholds:
    auto_approve_min_confidence: float
    auto_approve_min_relevance: float
    auto_approve_min_evidence_urls: int
    reject_max_relevance: float


def apply_classification(
    candidate: DiscoveredCompetitor, cls: CompanyClassification, allowed_categories: set[str]
) -> None:
    """Copy LLM 1's proposal onto the candidate, keeping only what evidence supports."""
    notes = candidate.validation_notes
    known_urls = {e.url for e in candidate.evidence} | {e.canonical_url for e in candidate.evidence}
    cited = [u for u in cls.evidence_urls if u in known_urls]
    if len(cited) < len(cls.evidence_urls):
        notes.append(f"Discarded {len(cls.evidence_urls) - len(cited)} cited URL(s) not present in the evidence")
    cats = [c for c in cls.relevant_categories if c in allowed_categories]
    if len(cats) < len(cls.relevant_categories):
        notes.append("Discarded categories outside the catalogue")

    name = (cls.company_name or "").strip() or None
    candidate.name_verified = bool(name and name_in_evidence(name, candidate.evidence))
    if name and not candidate.name_verified:
        notes.append(f"Company name {name!r} not found in evidence titles/snippets")

    candidate.company_name = name
    candidate.company_type = cls.company_type
    candidate.relevant_categories = cats
    candidate.uk_seller = cls.uk_seller
    candidate.sells_new_musical_instruments = cls.sells_new_musical_instruments
    candidate.relevance = cls.relevance
    candidate.confidence = cls.confidence
    candidate.rationale = cls.rationale.strip()[:RATIONALE_MAX_CHARS]
    candidate.cited_evidence_urls = cited
    candidate.proposed_competitor_class = cls.competitor_class
    candidate.classification_status = ClassificationStatus.COMPLETED


# --------------------------------------------------------------------------- decision


def is_known_marketplace(domain: str, marketplace_labels: tuple[str, ...]) -> bool:
    """True for e.g. amazon.co.uk / ebay.com: the registrable name is a known marketplace."""
    return domain.split(".", 1)[0] in marketplace_labels


def decide(candidate: DiscoveredCompetitor, t: Thresholds, marketplace_labels: tuple[str, ...] = ()) -> None:
    """Set approval_status, decision_reasons and price_benchmark. Deterministic."""
    reasons: list[str] = []
    c = candidate
    discovery_urls = {e.url for e in c.evidence if e.kind is EvidenceKind.DISCOVERY_RESULT}
    strong_uk = sorted(set(c.uk_signals) & STRONG_UK_SIGNALS)

    def finish(status: DomainApprovalStatus) -> None:
        c.approval_status = status
        c.decision_reasons = reasons
        # Only retailers can be price benchmarks; manufacturers/marketplaces/other never.
        c.price_benchmark = status is DomainApprovalStatus.AUTO_APPROVED and c.company_type in (
            CompanyType.RETAILER, CompanyType.SPECIALIST_RETAILER)  # fmt: skip

    if not discovery_urls:
        reasons.append("No discovery evidence")
        return finish(DomainApprovalStatus.REJECTED)
    if c.classification_status is not ClassificationStatus.COMPLETED or c.company_type is None:
        why = {
            ClassificationStatus.SKIPPED_LLM_DISABLED: "LLM classification skipped (LLM disabled)",
            ClassificationStatus.FAILED: "LLM classification failed",
            ClassificationStatus.MISSING: "LLM returned no classification",
        }.get(c.classification_status, "Classification unavailable")
        reasons.append(f"{why}; company type, relevance and confidence unknown. Needs manual review")
        return finish(DomainApprovalStatus.APPROVAL_REQUIRED)

    # Clear rejections: the evidence says this is not a UK shop for these products.
    if c.company_type is CompanyType.OTHER and (c.relevance or 0) <= t.reject_max_relevance:
        reasons.append(f"Not a shop (type 'other', relevance {c.relevance:.2f})")
        return finish(DomainApprovalStatus.REJECTED)
    if c.uk_seller is UkSeller.NO and not strong_uk:
        reasons.append("Evidence indicates it does not sell to the UK and no UK signal was found")
        return finish(DomainApprovalStatus.REJECTED)
    if (c.relevance or 0) <= t.reject_max_relevance and not c.products_matched:
        reasons.append(f"Low relevance ({c.relevance:.2f}) and no selected product found on the domain")
        return finish(DomainApprovalStatus.REJECTED)

    # Auto-approval needs every criterion; otherwise ask the user.
    unmet = []
    if c.company_type not in (CompanyType.RETAILER, CompanyType.SPECIALIST_RETAILER):
        unmet.append(f"type is {c.company_type.value} (only retailers are auto-approved)")
    if (c.confidence or 0) < t.auto_approve_min_confidence:
        unmet.append(f"confidence {c.confidence:.2f} < {t.auto_approve_min_confidence}")
    if (c.relevance or 0) < t.auto_approve_min_relevance:
        unmet.append(f"relevance {c.relevance:.2f} < {t.auto_approve_min_relevance}")
    if not c.name_verified:
        unmet.append("company name not verified in evidence")
    if not strong_uk:
        unmet.append("no strong UK signal (uk domain, GBP price or UK postcode)")
    if c.uk_seller is not UkSeller.YES:
        unmet.append(f"UK selling is '{c.uk_seller.value}'")
    if not c.products_matched:
        unmet.append("no page mentioning a selected product's model")
    if len(discovery_urls) < t.auto_approve_min_evidence_urls:
        unmet.append(f"{len(discovery_urls)} evidence URL(s) < {t.auto_approve_min_evidence_urls}")
    if not c.cited_evidence_urls:
        unmet.append("classification cited no valid evidence URL")
    if c.sells_new_musical_instruments is False:
        unmet.append("evidence suggests it does not sell new instruments")
    if is_known_marketplace(c.domain, marketplace_labels):
        unmet.append("domain is a known marketplace (never auto-approved as a retailer)")

    if unmet:
        reasons.extend(f"Needs approval: {u}" for u in unmet)
        return finish(DomainApprovalStatus.APPROVAL_REQUIRED)
    reasons.append(
        f"Auto-approved: {c.company_type.value}, confidence {c.confidence:.2f}, relevance {c.relevance:.2f}, "
        f"UK signals {strong_uk}, products {c.products_matched}"
    )
    return finish(DomainApprovalStatus.AUTO_APPROVED)


# --------------------------------------------------------------------------- competitor classification

# Types that are never a Direct Competitor and, being neither retailer nor marketplace,
# are Needs Review by definition.
_NEEDS_REVIEW_TYPES = {CompanyType.MANUFACTURER, CompanyType.DIRECTORY, CompanyType.OTHER}


def classify_competitor(candidate: DiscoveredCompetitor, marketplace_labels: tuple[str, ...] = ()) -> None:
    """Set the final competitor_class: LLM 1's proposal, subject to code safeguards.

    Deterministic and separate from `decide`: it never reads or changes approval,
    review decisions or price-benchmark eligibility. One override comes first: a
    known marketplace domain (`marketplace_labels`) is always Related Retailer,
    whatever LLM 1 proposed or whether it classified at all. Otherwise code only
    ever downgrades a proposal (to Related Retailer or Needs Review), never upgrades
    it, and records a reason for every downgrade.
    """
    c = candidate
    N, R, D = CompetitorClass.NEEDS_REVIEW, CompetitorClass.RELATED_RETAILER, CompetitorClass.DIRECT_COMPETITOR
    reasons: list[str] = []

    def finish(final: CompetitorClass) -> None:
        c.competitor_class = final
        c.competitor_class_reasons = reasons

    proposed = c.proposed_competitor_class
    if is_known_marketplace(c.domain, marketplace_labels):
        was = proposed.value if proposed else f"no proposal ({c.classification_status.value})"
        reasons.append(f"Known marketplace domain ({c.domain}): always RELATED_RETAILER, "
                       f"overriding the LLM 1 proposal ({was})")  # fmt: skip
        return finish(R)
    if c.classification_status is not ClassificationStatus.COMPLETED or c.company_type is None or proposed is None:
        reasons.append(f"No completed LLM 1 classification ({c.classification_status.value}): Needs Review")
        return finish(N)
    final = proposed
    reasons.append(f"LLM 1 proposed {proposed.value} (type {c.company_type.value})")

    def downgrade(to: CompetitorClass, why: str) -> None:
        nonlocal final
        if final is not to:
            reasons.append(f"Downgraded {final.value} -> {to.value}: {why}")
            final = to

    marketplace = c.company_type is CompanyType.MARKETPLACE or is_known_marketplace(c.domain, marketplace_labels)
    if marketplace:
        if final is D:
            downgrade(R, "a marketplace can never be a Direct Competitor")
    elif c.company_type in _NEEDS_REVIEW_TYPES:
        downgrade(N, f"type {c.company_type.value} cannot be a Direct Competitor or Related Retailer")
    elif final is not N:  # retailer or specialist retailer
        if c.uk_seller is not UkSeller.YES:
            downgrade(N, f"UK selling is '{c.uk_seller.value}', not confirmed")
        elif final is D:
            missing = []
            if not set(c.uk_signals) & STRONG_UK_SIGNALS:
                missing.append("no strong UK signal (uk domain, GBP price or UK postcode)")
            if not c.products_matched and not c.relevant_categories:
                missing.append("no selected product found on the domain and no supported catalogue category")
            if missing:
                downgrade(N, "; ".join(missing))

    # Any non-review class needs at least one cited URL that is in the evidence.
    if final is not N and not c.cited_evidence_urls:
        downgrade(N, "no valid cited evidence URL")
    if final is proposed:
        reasons.append(f"Accepted: {final.value}")
    return finish(final)


# --------------------------------------------------------------------------- reviewer decisions (Phase 4)


def apply_review_decision(candidate: DiscoveredCompetitor, decision: DomainDecisionRecord) -> None:
    """A persisted human decision overrides the run's automatic decision.

    REJECTED: never searched, never a benchmark. APPROVED: searched; a price
    benchmark only if the reviewer marked it eligible *and* the reviewed type
    is a retailer (the record itself already enforces this; checked again here).
    """
    c = candidate
    c.review_decision = decision
    c.automatic_approval_status = c.approval_status
    when = decision.decided_at.strftime("%Y-%m-%d %H:%M UTC")
    c.decision_reasons = [*c.decision_reasons,
                          f"Reviewer decision {decision.decision.value} by {decision.reviewer} at {when} "
                          f"(overrides automatic {c.approval_status.value})"]  # fmt: skip
    if decision.decision is ReviewDecision.REJECTED:
        c.approval_status = DomainApprovalStatus.REJECTED
        c.price_benchmark = False
    else:
        c.approval_status = DomainApprovalStatus.APPROVED
        c.price_benchmark = decision.benchmark_eligible and decision.company_type in BENCHMARK_ELIGIBLE_TYPES
