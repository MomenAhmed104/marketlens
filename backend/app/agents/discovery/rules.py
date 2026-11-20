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
    UkEvidenceAssessment,
    UkRelevanceEvidenceKind,
    UkRelevanceQuote,
    UkReview,
    UkSeller,
    VerifiedUkQuote,
)
from app.schemas.common import DomainApprovalStatus
from app.schemas.competitor import BENCHMARK_ELIGIBLE_TYPES, CompanyType
from app.schemas.review import DomainDecisionRecord, ReviewDecision
from app.services.text_match import gbp_prices, normalise_quote, quote_in_evidence
from app.services.urls import canonical_url

RATIONALE_MAX_CHARS = 600

# --------------------------------------------------------------------------- UK signals

_UK_POSTCODE = re.compile(r"\b(?:[A-Z]{1,2}\d[A-Z\d]?|GIR) ?\d[A-Z]{2}\b")
_UK_WORDS = re.compile(r"\b(?:UK|United Kingdom|England|Scotland|Wales|Northern Ireland|Great Britain)\b")
# Recorded signals for reviewers and competitor classification. They never establish UK
# relevance and are not an auto-approval requirement (user decision, 19 Nov 2026): approval
# relies on verified UK evidence quotes (`assess_uk_evidence`).
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


# --------------------------------------------------------------------------- UK relevance evidence

K = UkRelevanceEvidenceKind
MAX_UK_QUOTES = 20  # quotes beyond this are ignored (bounded work, bounded storage)
# Auto-approval needs supporting quotes from at least this many different evidence pages
# (user decision, 19 Nov 2026). Several quotes from one page count as one source.
AUTO_APPROVE_MIN_UK_SOURCES = 2

# Kinds that can show the business operates from the UK.
UK_BASED_KINDS = frozenset({K.UK_PHYSICAL_STORE, K.UK_ADDRESS, K.UK_COMPANY_REGISTRATION})

_I = re.IGNORECASE
_POSTCODE_ANY_CASE = re.compile(_UK_POSTCODE.pattern, _I)
_UK = r"(?:UK|U\.K\.|United Kingdom|Great Britain|Britain|England|Scotland|Wales|Northern Ireland)"
_UK_PLACE_WORDS = re.compile(rf"(?<![\w.]){_UK}(?![\w])", _I)
_DELIVERY_VERB = r"(?:deliver\w*|ship\w*|dispatch\w*|post|send|sell)"
_DELIVERY_WORDS = re.compile(rf"\b(?:{_DELIVERY_VERB}|delivery|shipping|postage|courier\w*)\b", _I)
_NEG = (r"(?:do not|don't|does not|doesn't|cannot|can't|can not|unable to|are unable to|won't|will not|"
        r"are not able to|no longer|never)")  # fmt: skip
_THE = r"(?:the\s+)?(?:mainland\s+)?"
_UK_WORD = rf"(?<![\w.]){_UK}(?![\w])"
_UK_END = rf"{_UK_WORD}(?=\s*(?:[.,;:!?)]|$|and\b|or\b))"  # the UK as a whole, not "the UK Highlands"
# Explicit statements that UK customers are NOT served. Generic "only"/"not" is never enough,
# and a restriction on some goods ("we don't ship batteries to the UK") is not one either.
_NO_UK_PATTERNS = [
    # "we do not ship to the UK", "we cannot deliver orders to the United Kingdom"
    re.compile(rf"\b{_NEG}\s+(?:currently\s+)?{_DELIVERY_VERB}\b(?:\s+(?:orders?|parcels?|goods|items?|products?|"
               rf"anything|internationally))?\s+(?:to|in|into|within)\s+{_THE}{_UK_WORD}", _I),
    # "no delivery to the UK", "not available in the UK"
    re.compile(rf"\b(?:no|not)\s+(?:currently\s+)?(?:available|offered|possible|delivery|deliveries|shipping|sales?)"
               rf"\s+(?:to|in|for|within)\s+{_THE}{_UK_WORD}", _I),
    # "UK orders are not accepted", "the UK is not served", "UK customers cannot order"
    re.compile(rf"{_UK_WORD}(?:\s+(?:orders|customers|addresses|deliveries|delivery|shipping))?\s+(?:is|are)\s+"
               rf"(?:currently\s+)?not\s+(?:currently\s+)?(?:accepted|served|supported|available|eligible|"
               rf"delivered to|shipped to)\b", _I),
    re.compile(rf"{_UK_WORD}\s+(?:orders|customers|addresses)\s+(?:cannot|can't|can not)\s+(?:be\s+)?"
               rf"(?:order\w*|served|shipped|delivered|accepted|placed|purchase\w*)\b", _I),
    # "we ship everywhere except the UK", "excluding the United Kingdom."
    re.compile(rf"\b(?:except|excluding|apart from|other than)\s+{_THE}{_UK_END}", _I),
]
# Wording that rules a quote out as positive UK delivery, without itself proving no UK service.
_OUTSIDE_UK = re.compile(rf"\boutside\s+{_THE}{_UK_WORD}", _I)
# A negation right before the delivery wording: "we do not deliver", "never ship", "no delivery".
_NEGATED_DELIVERY = re.compile(rf"\b(?:{_NEG}|not|no)\s+(?:\w+\s+)?(?:{_DELIVERY_VERB}|delivery|shipping)\b", _I)
# Physical, visitable premises. Strong cues stand on their own; weak cues ("shop in Leeds",
# "visit us") count only when nothing says the shop is online.
_STRONG_PHYSICAL = re.compile(
    r"\b(?:showroom|opening hours|opening times|open (?:mon|tue|wed|thu|fri|sat|sun|daily|7 days|\d)\w*|"
    r"premises|walk[- ]in|pop in(?:to)?|drop in|call in|in[- ]store|bricks[- ]and[- ]mortar|high street|"
    r"find us at|come and see us|located (?:at|on|in)|branch(?:es)?|our (?:shop|store) (?:is )?(?:at|on|in))\b", _I)
_VISIT_US = re.compile(r"\bvisit (?:us|our (?:shop|store)|the (?:shop|store))\b", _I)
_SHOP_IN_PLACE = re.compile(r"\b[Ss](?:hop|tore)\s+(?:in|on|at)\s+[A-Z]")  # place names are capitalised
_ONLINE = re.compile(r"\b(?:online (?:shop|store|retailer|music store|only)|web ?shop|e-?shop|online-only|"
                     r"shop online|buy online|visit us online|mail[- ]order)\b", _I)  # fmt: skip
# By-appointment showrooms are still visitable, so "by appointment" is not listed here.
_NOT_PHYSICAL = re.compile(r"\b(?:no (?:physical |retail |bricks[- ]and[- ]mortar )?(?:shop|store|showroom|premises)|"
                           r"online[- ]only|not open to the public|(?:do not|don't|does not|doesn't) have a "
                           r"(?:physical |retail )?(?:shop|store|showroom)|warehouse only|no (?:customer )?visits|"
                           r"no callers)\b", _I)  # fmt: skip
_REGISTRATION_WORDS = re.compile(r"\b(?:registered|company (?:no|number|reg)|companies house|registration)", _I)
_COMPANY_NUMBER = re.compile(r"\b(?:[A-Z]{2})?\d[\d ]{4,9}\d\b", _I)
# Text that is only prices, domains or "UK"-style wording is a weak signal, whatever it is labelled.
_WEAK_TOKENS = re.compile(r"£\s?[\d,.]+|\bGBP\b|\b[\w-]+(?:\.[\w-]+)*\.uk\b|" + _UK_PLACE_WORDS.pattern
                          + r"|[\d.,%]+", _I)  # fmt: skip


def _weak_only(text: str) -> bool:
    return not re.search(r"[a-z]{3,}", _WEAK_TOKENS.sub(" ", text), re.IGNORECASE)


def says_no_uk_service(text: str) -> bool:
    """True only for an explicit statement that UK customers are not served."""
    return any(p.search(text) for p in _NO_UK_PATTERNS)


def says_uk_delivery(text: str) -> bool:
    """Positive delivery or shipping to the UK: delivery wording, a UK place, and no
    negation of either ('we do not deliver to the UK' and 'outside the UK' do not count)."""
    return bool(_DELIVERY_WORDS.search(text) and _UK_PLACE_WORDS.search(text) and not says_no_uk_service(text)
                and not _NEGATED_DELIVERY.search(text) and not _OUTSIDE_UK.search(text))  # fmt: skip


def physical_store_problem(text: str) -> str | None:
    """Why the text does not describe a physical, visitable shop; None if it does."""
    if _NOT_PHYSICAL.search(text):
        return "the text says there is no visitable shop (online only, no premises, by appointment only, ...)"
    if _STRONG_PHYSICAL.search(text):
        return None
    if _ONLINE.search(text):
        return "an online store is not a physical shop"
    if _VISIT_US.search(text) or _SHOP_IN_PLACE.search(text):
        return None
    return "a physical store quote must describe a visitable shop, showroom or opening hours"


def _kind_problem(kind: UkRelevanceEvidenceKind, quote: str) -> str | None:
    """Why a verified quote's text does not fit its claimed kind; None if it does.

    These are deliberately simple, conservative checks against mislabelled quotes (e.g.
    a price cited as a UK address, or "we do not deliver to the UK" cited as UK
    delivery). They do not prove the claim; the quote does that. When unsure they
    refuse, so the candidate stays reviewable."""
    if kind is K.WEAK_SIGNAL:
        return "weak signal: recorded, never sufficient"
    if _weak_only(quote):
        return "only prices, domains or UK wording: a weak signal"
    if kind is K.UK_PHYSICAL_STORE:
        return physical_store_problem(quote)
    if kind is K.UK_ADDRESS and not _POSTCODE_ANY_CASE.search(quote):
        return "a UK address quote must contain a UK postcode"
    if kind is K.UK_COMPANY_REGISTRATION and not (_REGISTRATION_WORDS.search(quote) and _COMPANY_NUMBER.search(quote)):
        return "a company registration quote must say 'registered'/'company number' and give the number"
    if kind is K.UK_DELIVERY and not says_uk_delivery(quote):
        return "a UK delivery quote must positively say it delivers or ships to the UK (no negation)"
    if kind is K.NO_UK_SERVICE and not says_no_uk_service(quote):
        return "a no-UK-service quote must explicitly say the UK is not delivered to or served"
    return None


def verify_uk_quotes(quotes: list[UkRelevanceQuote], evidence: list[EvidenceItem]) -> list[VerifiedUkQuote]:
    """Each cited quote, checked against the candidate's own stored evidence. Duplicates
    (same kind, same normalised text) count once."""
    out: list[VerifiedUkQuote] = []
    seen: set[tuple[K, str]] = set()
    for q in quotes[:MAX_UK_QUOTES]:
        key = (q.kind, normalise_quote(q.quote))
        if key in seen:
            continue
        seen.add(key)
        if not quote_in_evidence(q.quote, q.url, evidence):
            note = "not found verbatim in the stored title or snippet of that URL"
            out.append(VerifiedUkQuote(**q.model_dump(), verified=False, supports=False, note=note))
            continue
        problem = _kind_problem(q.kind, q.quote)
        out.append(VerifiedUkQuote(**q.model_dump(), verified=True, supports=problem is None, note=problem))
    return out


def _source_key(url: str) -> str:
    try:
        return canonical_url(url)
    except ValueError:
        return url.strip()


# The kinds that make a supporting quote evidence *of* UK relevance (not against it).
UK_RELEVANCE_KINDS = UK_BASED_KINDS | {K.UK_DELIVERY, K.NON_UK_OPERATOR}


def summarise_uk_evidence(quotes: list[VerifiedUkQuote]) -> dict:
    """Sufficiency, contradiction, mixed location and independent sources, from the verified,
    supporting quotes only. Deterministic; used when assessing and again when reviewing, so a
    stored flag is never trusted on its own."""
    supporting = [q for q in quotes if q.verified and q.supports]
    kinds = {q.kind for q in supporting}
    based = bool(kinds & UK_BASED_KINDS)
    return dict(
        sufficient=based or (K.NON_UK_OPERATOR in kinds and K.UK_DELIVERY in kinds),
        contradicted=K.NO_UK_SERVICE in kinds,
        mixed_location=based and K.NON_UK_OPERATOR in kinds,
        independent_sources=len({_source_key(q.url) for q in supporting if q.kind in UK_RELEVANCE_KINDS}),
    )


def assess_uk_evidence(quotes: list[UkRelevanceQuote], evidence: list[EvidenceItem]) -> UkEvidenceAssessment:
    """Check LLM 1's UK evidence quotes and decide whether they suffice. Deterministic.

    There is no UK category: the result says what the verified evidence shows.
    - sufficient: a supporting UK store, UK address or UK company registration quote; or a
      supporting based-abroad (non-UK operator) quote *and* a supporting UK delivery quote.
    - contradicted: a supporting quote explicitly says the UK is not delivered to or served.
    - mixed_location: UK-presence evidence and a based-abroad quote at the same time.
    - independent_sources: distinct evidence pages among the quotes that support UK relevance.
    `evidence` in the result holds every supporting quote (including contradicting ones),
    `unverified_claims` holds the quotes that did not count. Weak signals (GBP, .uk, "UK"
    wording) never count, whatever kind they are labelled.
    """
    reasons = []
    if len(quotes) > MAX_UK_QUOTES:
        reasons.append(f"Ignored {len(quotes) - MAX_UK_QUOTES} quote(s) beyond the first {MAX_UK_QUOTES}")
    checked = verify_uk_quotes(quotes, evidence)
    unverified = sum(not q.verified for q in checked)
    if unverified:
        reasons.append(f"{unverified} quote(s) not found in the evidence: ignored")
    misfit = sum(q.verified and not q.supports for q in checked)
    if misfit:
        reasons.append(f"{misfit} verified quote(s) do not count (weak signal or text does not fit its kind)")
    summary = summarise_uk_evidence(checked)
    sufficient, contradicted = summary["sufficient"], summary["contradicted"]
    mixed, sources = summary["mixed_location"], summary["independent_sources"]
    kinds = {q.kind for q in checked if q.verified and q.supports}
    based = bool(kinds & UK_BASED_KINDS)
    abroad_serving = K.NON_UK_OPERATOR in kinds and K.UK_DELIVERY in kinds

    if based:
        reasons.append("Verified UK presence: a UK shop, UK address or UK company registration")
    if abroad_serving:
        reasons.append("Verified: based outside the UK and delivers to the UK")
    if not sufficient:
        reasons.append("Insufficient verified UK evidence (needs a UK shop, address or company registration, or "
                       "a based-abroad quote together with a UK delivery quote)")  # fmt: skip
    if contradicted:
        reasons.append("Contradiction: a verified quote says the UK is not delivered to or served")
    if mixed:
        reasons.append("Mixed: verified UK presence and a verified based-abroad quote")
    reasons.append(f"Supporting quotes come from {sources} independent evidence page(s)")
    return UkEvidenceAssessment(evidence=checked, sufficient=sufficient, contradicted=contradicted,
                                mixed_location=mixed, independent_sources=sources, reasons=reasons)  # fmt: skip


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
    # UK relevance evidence: its own field, separate from type, class, approval and
    # benchmarking. Always assessed for a completed classification: no quotes means
    # insufficient evidence (never "not assessed"), so missing evidence is never trusted.
    candidate.uk_assessment = uk_assessment_from(cls, candidate.evidence)
    seller, why = reconcile_uk_seller(cls.uk_seller, candidate.uk_assessment)
    candidate.uk_seller = seller
    if why:
        notes.append(why)


def reconcile_uk_seller(proposed: UkSeller, a: UkEvidenceAssessment) -> tuple[UkSeller, str | None]:
    """`uk_seller` made consistent with the verified UK evidence. Deterministic.

    - yes: only when the verified evidence is sufficient, not contradicted, and LLM 1 also
      said yes. A .uk domain or GBP prices alone never verify UK selling.
    - no: only when a verified quote says the UK is not served and nothing verified shows
      UK relevance.
    - unknown: everything else, including an unverified yes or no, or mixed evidence.
    Returns the value and, if it differs from LLM 1's, the reason."""
    if a.sufficient and not a.contradicted:
        final = UkSeller.YES if proposed is UkSeller.YES else UkSeller.UNKNOWN
    elif a.contradicted and not a.sufficient:
        final = UkSeller.NO
    else:
        final = UkSeller.UNKNOWN
    if final is proposed:
        return final, None
    state = ("sufficient" if a.sufficient else "insufficient") + (", contradicted" if a.contradicted else "")
    return final, f"uk_seller '{proposed.value}' set to '{final.value}' to match the verified UK evidence ({state})"


MAX_LLM_UK_QUOTES = 5  # LLM 1 is asked for at most this many quotes per domain
MAX_STORED_UK_TEXT = 500  # stored quote/note length, applied after verification


def _clip(text: str | None) -> str | None:
    return text if text is None or len(text) <= MAX_STORED_UK_TEXT else text[: MAX_STORED_UK_TEXT - 1] + "…"


def uk_assessment_from(cls: CompanyClassification, evidence: list[EvidenceItem]) -> UkEvidenceAssessment:
    """The stored UK evidence assessment for one LLM 1 classification. Deterministic.

    Quotes are verified against all of the domain's stored evidence (`assess_uk_evidence`).
    Supporting quotes are the evidence; the others are kept apart as `unverified_claims`
    (reviewer context, never evidence). Both are clipped to MAX_STORED_UK_TEXT after
    verification. The reasons say what was discarded and what the UK answer cleaning replaced."""
    quotes = cls.uk_evidence[:MAX_LLM_UK_QUOTES]
    a = assess_uk_evidence(quotes, evidence)
    reasons = [f"UK answer cleaned: {w}" for w in cls.uk_validation_warnings]
    if len(cls.uk_evidence) > MAX_LLM_UK_QUOTES:
        reasons.append(f"Ignored {len(cls.uk_evidence) - MAX_LLM_UK_QUOTES} UK quote(s) beyond the first "
                       f"{MAX_LLM_UK_QUOTES}")  # fmt: skip
    clipped = [q.model_copy(update={"quote": _clip(q.quote), "note": _clip(q.note)}) for q in a.evidence]
    kept = [q for q in clipped if q.supports]
    claims = [q for q in clipped if not q.supports]
    if claims:
        reasons.append(f"Stored {len(kept)} supporting quote(s); discarded {len(claims)} from the evidence "
                       "(kept separately as unverified claims for the reviewer)")  # fmt: skip
    return a.model_copy(update={"evidence": kept, "unverified_claims": claims,
                                "reasons": [*reasons, *a.reasons]})  # fmt: skip


# --------------------------------------------------------------------------- decision


def is_known_marketplace(domain: str, marketplace_labels: tuple[str, ...]) -> bool:
    """True for e.g. amazon.co.uk / ebay.com: the registrable name is a known marketplace."""
    return domain.split(".", 1)[0] in marketplace_labels


def decide(candidate: DiscoveredCompetitor, t: Thresholds, marketplace_labels: tuple[str, ...] = ()) -> None:
    """Set approval_status, decision_reasons and price_benchmark. Deterministic."""
    reasons: list[str] = []
    c = candidate
    discovery_urls = {e.url for e in c.evidence if e.kind is EvidenceKind.DISCOVERY_RESULT}

    def finish(status: DomainApprovalStatus) -> None:
        c.approval_status = status
        c.decision_reasons = reasons
        # Automatic decisions never grant benchmark permission (user decision, 18-19 Nov 2026),
        # not even auto-approval. A discovered domain becomes a benchmark only through a
        # reviewer's explicit `benchmark_eligible` (apply_review_decision, which still enforces
        # the type rules); configured competitors keep their workbook flag.
        c.price_benchmark = False

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

    # Verified "does not serve the UK" (decision D4): never rejected or dropped automatically,
    # and never auto-approved. A reviewer decides; the contradiction stays visible in review.
    uk = c.uk_assessment
    if uk is not None and uk.contradicted:
        reasons.append("Verified evidence says it does not deliver to or serve the UK (contradictory UK "
                       "evidence). Needs a reviewer decision (not rejected automatically)")  # fmt: skip
        return finish(DomainApprovalStatus.APPROVAL_REQUIRED)

    # Clear rejections: the evidence says this is not a UK shop for these products.
    if c.company_type is CompanyType.OTHER and (c.relevance or 0) <= t.reject_max_relevance:
        reasons.append(f"Not a shop (type 'other', relevance {c.relevance:.2f})")
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
    # UK relevance: verified evidence quotes only. A .uk domain, GBP prices or a postcode
    # signal are never required and never sufficient (user decision, 19 Nov 2026), so an
    # overseas retailer that demonstrably serves the UK is not disadvantaged.
    if uk is None:
        unmet.append("no verified UK evidence assessment")
    else:
        if not uk.sufficient:
            unmet.append("insufficient verified UK evidence")
        if uk.mixed_location:
            unmet.append("mixed UK evidence (UK presence and based abroad)")
        if uk.independent_sources < AUTO_APPROVE_MIN_UK_SOURCES:
            unmet.append(f"UK evidence from {uk.independent_sources} independent page(s) "
                         f"< {AUTO_APPROVE_MIN_UK_SOURCES}")  # fmt: skip
    if c.uk_seller is not UkSeller.YES:
        unmet.append(f"UK selling is '{c.uk_seller.value}'")
    if not c.products_matched:
        unmet.append("no page mentioning a selected product's model")
    if len(discovery_urls) < t.auto_approve_min_evidence_urls:
        # Counts distinct pages the domain appeared on in the GLOBAL product searches (a relevance
        # signal). Domain-profile pages (about/contact/terms, found by the domain-restricted profile
        # search) supply UK evidence and count for `independent_sources`, never here (post-Stage 7
        # diagnostic; the rule itself is unchanged).
        profile_urls = {e.url for e in c.evidence if e.kind is EvidenceKind.DOMAIN_PROFILE} - discovery_urls
        unmet.append(f"{len(discovery_urls)} discovery evidence URL(s) < {t.auto_approve_min_evidence_urls}: the "
                     f"domain appeared on {len(discovery_urls)} distinct page(s) in the global product searches; "
                     f"its {len(profile_urls)} domain-profile page(s) (used for UK evidence) do not count here")  # fmt: skip
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
        f"verified UK evidence from {uk.independent_sources} independent page(s), products {c.products_matched}"
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
    is in BENCHMARK_ELIGIBLE_TYPES (retailer, specialist retailer or manufacturer;
    the record itself already enforces this; checked again here).
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


# --------------------------------------------------------------------------- UK review view


def _missing_evidence(a: UkEvidenceAssessment | None) -> list[str]:
    """What would settle an uncertain case, from what the verified evidence already shows."""
    kinds = {q.kind for q in (a.evidence if a else []) if q.supports}
    out = []
    if not kinds & UK_BASED_KINDS:
        out.append("For a UK-based business: a shop or showroom customers can visit, a UK address with a "
                   "postcode, or a UK company registration with its number")  # fmt: skip
    if K.NON_UK_OPERATOR in kinds and K.UK_DELIVERY not in kinds:
        out.append("It appears to be based outside the UK: a statement that it delivers or ships to the UK")
    elif K.UK_DELIVERY in kinds and K.NON_UK_OPERATOR not in kinds:
        out.append("It mentions UK delivery: where the business itself is based")
    elif not kinds & {K.NON_UK_OPERATOR, K.UK_DELIVERY}:
        out.append("For a business abroad: where it is based and a statement that it delivers to the UK")
    out.append("GBP prices, a .uk domain or the word 'UK' on their own are not enough")
    return out


def _decision_line(d: DomainDecisionRecord | None, s: dict | None) -> str:
    """How the reviewer's decision relates to uncertain or contradictory evidence ('' if not relevant)."""
    if d is None or d.decision is not ReviewDecision.APPROVED:
        return ""
    when = d.decided_at.strftime("%Y-%m-%d")
    if s is not None and s["contradicted"]:
        return f" {d.reviewer} approved it on {when} despite the contradictory evidence."
    if s is None or not s["sufficient"]:
        return f" {d.reviewer} approved it on {when} without sufficient verified UK evidence."
    return ""


def uk_review(assessment: UkEvidenceAssessment | None, decision: DomainDecisionRecord | None = None) -> UkReview:
    """The UK review view of one domain. Deterministic; never stored.

    Uses only the Python-verified evidence: sufficiency, contradiction and sources are
    recomputed from the stored supporting quotes (`summarise_uk_evidence`), never taken from
    stored flags alone. The reviewer's decision never changes the evidence or the
    contradiction shown; it is only mentioned in the summary. It never approves, rejects or
    changes benchmark permission.
    """
    a = assessment
    supporting = [q for q in (a.evidence if a else []) if q.verified and q.supports]
    line = _decision_line(decision, summarise_uk_evidence(supporting) if a else None)
    if a is None:
        return UkReview(assessed=False, sufficient=False, contradicted=False, mixed_location=False,
                        independent_sources=0,
                        summary="Not assessed: there is no UK evidence assessment for this domain (LLM 1 was not "
                                "used, failed, or the record is older). Check the evidence and decide." + line,
                        missing_evidence=_missing_evidence(None))  # fmt: skip
    s = summarise_uk_evidence(supporting)
    common = dict(assessed=True, **s, reasons=list(a.reasons), verified_evidence=supporting,
                  unverified_claims=list(a.unverified_claims))  # fmt: skip
    pages = f"{s['independent_sources']} independent page(s)"
    if s["contradicted"]:
        lead = ("Contradictory UK evidence: a verified quote says it does not deliver to or serve the UK"
                + (", while other verified evidence shows UK relevance." if s["sufficient"] else ".")
                + " Needs your review.")  # fmt: skip
        return UkReview(summary=lead + line, missing_evidence=_missing_evidence(a), **common)
    if s["sufficient"] and s["mixed_location"]:
        return UkReview(summary=f"Verified UK presence ({pages}), but also a verified quote that the business is "
                                "based outside the UK. Check which applies; approval is your decision." + line,
                        **common)  # fmt: skip
    if s["sufficient"]:
        return UkReview(summary=f"Verified UK relevance evidence from {pages}. Approval is still a reviewer "
                                "decision." + line, **common)  # fmt: skip
    return UkReview(summary="Insufficient verified UK evidence: nothing verified shows a UK shop, address or "
                            "company, or delivery to the UK from abroad. Needs your review." + line,
                    missing_evidence=_missing_evidence(a), **common)  # fmt: skip
