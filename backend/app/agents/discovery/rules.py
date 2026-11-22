"""Deterministic discovery rules: validation, automatic search eligibility and classification.

LLM 1 proposes company names, types, categories and (informational) scores. Everything in
this module is plain Python, so every decision is reproducible and explainable.

Stage 8.1 (user decision, 22 Nov 2026): UK relevance comes from LLM 1's UK-focused search
strategy. There is no UK verification, UK evidence scoring or UK approval gate. LLM 1's
`serves_uk_customers` answer, relevance and confidence are recorded for people to read and
never affect eligibility, approval, exclusion or competitor class.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agents.discovery.schemas import (
    CLASSIFICATION_VERSION,
    ClassificationStatus,
    CompanyClassification,
    CompetitorClass,
    DiscoveredCompetitor,
    EvidenceItem,
    EvidenceKind,
)
from app.schemas.common import DomainApprovalStatus
from app.schemas.competitor import BENCHMARK_ELIGIBLE_TYPES, CompanyType
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.services.domains import canonical_domain

RATIONALE_MAX_CHARS = 600
NOTE_MAX_CHARS = 300  # serves_uk_customers_note


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

    note = (cls.serves_uk_customers_note or "").strip()[:NOTE_MAX_CHARS] or None
    candidate.company_name = name
    candidate.company_type = cls.company_type
    candidate.relevant_categories = cats
    candidate.serves_uk_customers = cls.serves_uk_customers  # informational only, never checked or used
    candidate.serves_uk_customers_note = note
    candidate.sells_new_musical_instruments = cls.sells_new_musical_instruments
    candidate.relevance = cls.relevance
    candidate.confidence = cls.confidence
    candidate.rationale = cls.rationale.strip()[:RATIONALE_MAX_CHARS]
    candidate.cited_evidence_urls = cited
    candidate.proposed_competitor_class = cls.competitor_class
    candidate.classification_status = ClassificationStatus.COMPLETED
    candidate.classification_version = CLASSIFICATION_VERSION


# --------------------------------------------------------------------------- automatic search eligibility


def is_known_marketplace(domain: str, marketplace_labels: tuple[str, ...]) -> bool:
    """True for e.g. amazon.co.uk / ebay.com: the registrable name is a known marketplace."""
    return domain.split(".", 1)[0] in marketplace_labels


# The only company types that can become search targets without a person approving them.
AUTO_SEARCH_TYPES = frozenset({CompanyType.RETAILER, CompanyType.SPECIALIST_RETAILER})


@dataclass(frozen=True)
class EligibilityPolicy:
    """Settings-derived inputs of `is_search_eligible` (see `policy_from_settings`)."""

    excluded_domains: frozenset[str] = field(default_factory=frozenset)  # own site, platforms, removed competitors
    marketplace_labels: tuple[str, ...] = ()


def policy_from_settings(settings) -> EligibilityPolicy:
    excluded = (*settings.own_domains, *settings.discovery_excluded_domains,
                *settings.discovery_excluded_competitor_domains)  # fmt: skip
    return EligibilityPolicy(excluded_domains=frozenset(d.strip().lower() for d in excluded),
                             marketplace_labels=tuple(settings.marketplace_domain_labels))  # fmt: skip


@dataclass(frozen=True)
class SearchEligibility:
    eligible: bool
    reasons: list[str]  # why it qualifies, or every criterion it does not meet


def is_search_eligible(
    candidate: DiscoveredCompetitor,
    decision: DomainDecisionRecord | None = None,
    policy: EligibilityPolicy = EligibilityPolicy(),
) -> SearchEligibility:
    """Whether a discovered domain may be searched for products without a person approving it.

    Deterministic, and recomputed from the (stored) candidate record and the current human
    decision every time it is needed; nothing is written for it. All of these must hold:
      1. LLM 1 completed its classification, at the current CLASSIFICATION_VERSION (older
         classifications need rediscovery first);
      2. the company type is retailer or specialist_retailer;
      3. the company name is written in the domain's discovery evidence;
      4. at least one URL LLM 1 cited is in the domain's discovery evidence;
      5. the domain is a valid, normalised registrable domain;
      6. it is not an excluded domain (own site, platforms, removed competitors);
      7. it is not a known marketplace or directory;
      8. no person has rejected it;
      9. it is not confirmed to sell only used instruments (`sells_new_musical_instruments`
         is False). Unknown (None) never blocks; a used-only retailer can still be approved
         by a reviewer, which never grants benchmark permission on its own.
    Never used: serves_uk_customers (even "no"), relevance, confidence, a .uk domain, GBP
    prices or a UK address. Eligibility never grants price-benchmark permission.
    """
    c = candidate
    unmet: list[str] = []
    if decision is not None and decision.decision is ReviewDecision.REJECTED:
        unmet.append("rejected by a reviewer")
    if c.classification_status is not ClassificationStatus.COMPLETED or c.company_type is None:
        unmet.append(f"no completed LLM 1 classification ({c.classification_status.value})")
    elif c.classification_version != CLASSIFICATION_VERSION:
        unmet.append("classified before the current discovery prompt; needs rediscovery")
    if c.company_type is not None and c.company_type not in AUTO_SEARCH_TYPES:
        unmet.append(f"type is {c.company_type.value} (only retailers and specialist retailers are "
                     "searched automatically)")  # fmt: skip
    if not (c.company_name and c.name_verified):
        unmet.append("company name not found in the discovery evidence")
    evidence_urls = {e.url for e in c.evidence} | {e.canonical_url for e in c.evidence}
    if not any(u in evidence_urls for u in c.cited_evidence_urls):
        unmet.append("no cited URL is in the discovery evidence")
    try:
        valid = canonical_domain(c.domain) == c.domain
    except ValueError:
        valid = False
    if not valid:
        unmet.append(f"{c.domain!r} is not a valid normalised domain")
    elif c.domain in policy.excluded_domains:
        unmet.append("excluded domain")
    if is_known_marketplace(c.domain, policy.marketplace_labels):
        unmet.append("known marketplace domain")
    if c.sells_new_musical_instruments is False:
        unmet.append("the evidence shows it sells only used instruments (used-only retailers are not searched "
                     "automatically; a reviewer can approve it)")
    if unmet:
        return SearchEligibility(False, unmet)
    return SearchEligibility(True, [f"Automatically searchable: {c.company_type.value} named in the discovery "
                                    f"evidence ({c.company_name}), with cited evidence"])  # fmt: skip


def decide(candidate: DiscoveredCompetitor, policy: EligibilityPolicy = EligibilityPolicy()) -> None:
    """Set approval_status, decision_reasons and price_benchmark for this run. Deterministic.

    AUTO_APPROVED means automatically search-eligible (`is_search_eligible`); a reviewer
    decision applied afterwards (`apply_review_decision`) always wins.
    """
    c = candidate
    # Automatic decisions never grant benchmark permission (user decisions 18-19 Nov 2026,
    # kept in Stage 8.1). Only a reviewer's explicit `benchmark_eligible` or the workbook flag.
    c.price_benchmark = False
    if not any(e.kind is EvidenceKind.DISCOVERY_RESULT for e in c.evidence):
        c.approval_status, c.decision_reasons = DomainApprovalStatus.REJECTED, ["No discovery evidence"]
        return
    if c.classification_status is not ClassificationStatus.COMPLETED or c.company_type is None:
        why = {
            ClassificationStatus.SKIPPED_LLM_DISABLED: "LLM classification skipped (LLM disabled)",
            ClassificationStatus.FAILED: "LLM classification failed",
            ClassificationStatus.MISSING: "LLM returned no classification",
        }.get(c.classification_status, "Classification unavailable")
        c.approval_status = DomainApprovalStatus.APPROVAL_REQUIRED
        c.decision_reasons = [f"{why}; company type unknown, so it is not searched automatically. Needs review"]
        return
    e = is_search_eligible(c, None, policy)
    if e.eligible:
        c.approval_status, c.decision_reasons = DomainApprovalStatus.AUTO_APPROVED, list(e.reasons)
    else:
        c.approval_status = DomainApprovalStatus.APPROVAL_REQUIRED
        c.decision_reasons = [f"Needs review: {r}" for r in e.reasons]


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
    it, and records a reason for every downgrade. No UK check (Stage 8.1).
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

    if c.company_type is CompanyType.MARKETPLACE:
        if final is D:
            downgrade(R, "a marketplace can never be a Direct Competitor")
    elif c.company_type in _NEEDS_REVIEW_TYPES:
        downgrade(N, f"type {c.company_type.value} cannot be a Direct Competitor or Related Retailer")
    elif final is D and not c.products_matched and not c.relevant_categories:  # retailer or specialist retailer
        downgrade(N, "no selected product found on the domain and no supported catalogue category")

    # Any non-review class needs at least one cited URL that is in the evidence.
    if final is not N and not c.cited_evidence_urls:
        downgrade(N, "no valid cited evidence URL")
    if final is proposed:
        reasons.append(f"Accepted: {final.value}")
    return finish(final)


# --------------------------------------------------------------------------- reviewer decisions (Phase 4)


def apply_review_decision(candidate: DiscoveredCompetitor, decision: DomainDecisionRecord) -> None:
    """A persisted human decision overrides the run's automatic decision.

    REJECTED: never searched, never a benchmark (even when automatically eligible).
    APPROVED: searched; a price benchmark only if the reviewer marked it eligible *and*
    the reviewed type is in BENCHMARK_ELIGIBLE_TYPES (retailer, specialist retailer or
    manufacturer; the record itself already enforces this; checked again here).
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
