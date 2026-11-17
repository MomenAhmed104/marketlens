"""Phase 6 product matching (deterministic).

For each Stompbox product and Phase 5 candidate:
  1. Gather identity evidence in three independent groups: titles (retrieved
     page title, search title, extracted title), URL paths, and extracted
     model/SKU fields. Extracted fields exist only when LLM 2 ran and their
     quotes were verified in Phase 5. Search snippets are never used.
  2. Check page kind, brand, model (incl. generation and sibling models),
     critical variants and condition.
  3. Decide EXACT_MATCH / SIMILAR_MATCH / REVIEW_REQUIRED / NO_MATCH by fixed
     rules (see `decide`). Title similarity alone never yields EXACT_MATCH:
     the exact model identifier must be found in at least two evidence groups.
  4. Validate the price separately (`price.validate_price`) and decide whether
     the pair qualifies for direct UK price comparison.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlsplit

from app.agents.intelligence.schemas import (
    CompetitorProduct,
    ExtractionStatus,
    IntelligenceRunResult,
    PageType,
    ProductIntelligence,
)
from app.schemas.catalogue import StompboxProduct
from app.schemas.common import EvidenceStatus, MatchStatus
from app.schemas.search import RetrievalStatus
from app.services.matching import attributes as A
from app.services.matching.price import validate_price
from app.services.matching.schemas import (
    AttributeCheck,
    CandidateMatch,
    CheckResult,
    Condition,
    ConditionCheck,
    EvidenceRef,
    EvidenceSource,
    MatchConfidence,
    MatchingRunResult,
    PageCheck,
    PageKind,
    PriceComparison,
    PriceStatus,
    PriceValidation,
    ProductMatchSummary,
)
from app.services.text_match import url_text
from app.services.uk_gate import benchmark_block_reason

MAX_REF_CHARS = 200
# An UNVERIFIED result on these blocks EXACT_MATCH (the Stompbox product defines
# them and listings normally state them). Others only block on CONFLICT.
CONFIRM_REQUIRED = {"handedness", "finish", "generation", "edition", "configuration"}

# URLs that are not single-product pages (used when LLM 2 did not classify the page).
_NON_PRODUCT_HOST = re.compile(r"^(?:forum|forums|blog|community|support|help|news|learn)\.", re.IGNORECASE)
_NON_PRODUCT_PATH = re.compile(
    r"(?:^|/)(?:blog|blogs|learn|news|forum|forums|topic|topics|guide|guides|reviews?|articles?|compare|search"
    r"|category|categories|brands?|sale|deals|offers)(?:/|$)|[-_]guide(?:/|$|[-_.])|[-_]vs[-_]",
    re.IGNORECASE,
)


def _ref(source: EvidenceSource, text: str) -> EvidenceRef:
    return EvidenceRef(source=source, text=" ".join(text.split())[:MAX_REF_CHARS])


def _url_words(url: str) -> str:
    return re.sub(r"[-_/+.]+", " ", url_text(url)).strip()


class Evidence:
    """Identity texts for one candidate, grouped by independence."""

    def __init__(self, c: CompetitorProduct):
        self.titles: list[EvidenceRef] = []
        if c.page.title and c.page.status is RetrievalStatus.OK:
            self.titles.append(_ref(EvidenceSource.PAGE_TITLE, c.page.title))
        if c.search.title:
            self.titles.append(_ref(EvidenceSource.SEARCH_TITLE, c.search.title))
        if c.product_title:
            self.titles.append(_ref(EvidenceSource.EXTRACTED_TITLE, c.product_title))
        self.urls = [_ref(EvidenceSource.URL, w) for w in dict.fromkeys(
            _url_words(u) for u in (c.product_url, c.search.url)) if w]  # fmt: skip
        self.fields = [_ref(src, v) for src, v in ((EvidenceSource.EXTRACTED_MODEL, c.model),
                                                   (EvidenceSource.EXTRACTED_SKU, c.sku)) if v]  # fmt: skip
        self.brand_field = _ref(EvidenceSource.EXTRACTED_BRAND, c.brand) if c.brand else None
        self.availability = _ref(EvidenceSource.AVAILABILITY, c.availability) if c.availability else None

    @property
    def identity(self) -> list[EvidenceRef]:
        return self.titles + self.urls + self.fields

    @property
    def text(self) -> str:
        return " | ".join(r.text for r in self.identity)


def _group(ref: EvidenceRef) -> str:
    if ref.source in (EvidenceSource.EXTRACTED_MODEL, EvidenceSource.EXTRACTED_SKU):
        return "field"
    return "url" if ref.source is EvidenceSource.URL else "title"


# --------------------------------------------------------------------------- checks


def check_page(c: CompetitorProduct) -> PageCheck:
    retrieved = c.page.status is RetrievalStatus.OK
    if c.extraction_status is ExtractionStatus.COMPLETED and c.page_type is not None:
        kind = PageKind.PRODUCT_PAGE if c.page_type is PageType.PRODUCT_PAGE else PageKind.NON_PRODUCT_PAGE
        return PageCheck(kind=kind, retrieved=retrieved, basis=f"LLM 2 page type: {c.page_type.value}")
    parts = urlsplit(c.product_url)
    if _NON_PRODUCT_HOST.match(parts.hostname or "") or _NON_PRODUCT_PATH.search(parts.path):
        return PageCheck(kind=PageKind.NON_PRODUCT_PAGE, retrieved=retrieved,
                         basis=f"URL looks like a blog/forum/guide/category page: {c.product_url}")  # fmt: skip
    return PageCheck(kind=PageKind.UNKNOWN, retrieved=retrieved,
                     basis="Page type not classified (no LLM extraction); URL does not indicate a non-product page")  # fmt: skip


def check_brand(p: StompboxProduct, ev: Evidence) -> AttributeCheck:
    found = [r for r in ev.identity if A.brand_in(p.brand, r.text)]
    bf = ev.brand_field
    if bf and not A.brand_in(p.brand, bf.text) and not A.brand_in(bf.text, p.brand):
        return AttributeCheck(attribute="brand", result=CheckResult.CONFLICT, critical=True, expected=p.brand,
                              found=[bf.text], evidence=[bf], note="Extracted brand differs from the Stompbox brand")  # fmt: skip
    if bf and A.brand_in(p.brand, bf.text):
        found.append(bf)
    if found:
        return AttributeCheck(attribute="brand", result=CheckResult.MATCH, critical=True, expected=p.brand,
                              found=sorted({r.text for r in found}), evidence=found)  # fmt: skip
    return AttributeCheck(attribute="brand", result=CheckResult.UNVERIFIED, critical=True, expected=p.brand,
                          note="Brand not found in titles, URL or extracted fields")  # fmt: skip


def check_model(p: StompboxProduct, ev: Evidence) -> tuple[AttributeCheck, set[str], set[str]]:
    """Returns (check, matched evidence groups, sibling models seen)."""
    model = p.manufacturer_model
    matched = [r for r in ev.identity if A.model_in(model, r.text)]
    siblings = set().union(*(A.sibling_models(model, r.text) for r in ev.identity)) if ev.identity else set()
    extracted_conflict = [r for r in ev.fields if r.source is EvidenceSource.EXTRACTED_MODEL
                          and not A.model_in(model, r.text) and not A.core_in(r.text, model)]  # fmt: skip
    groups = {_group(r) for r in matched}
    if extracted_conflict:
        return AttributeCheck(attribute="model", result=CheckResult.CONFLICT, critical=True, expected=model,
                              found=[r.text for r in extracted_conflict], evidence=extracted_conflict,
                              note="Extracted model is a different model"), set(), siblings  # fmt: skip
    if matched:
        note = f"Model found in {len(groups)} independent evidence group(s): {sorted(groups)}"
        return AttributeCheck(attribute="model", result=CheckResult.MATCH, critical=True, expected=model,
                              found=sorted({r.text for r in matched}), evidence=matched, note=note), groups, siblings  # fmt: skip
    if siblings:
        sib_refs = [r for r in ev.identity if A.sibling_models(model, r.text)]
        return AttributeCheck(attribute="model", result=CheckResult.CONFLICT, critical=True, expected=model,
                              found=sorted(siblings), evidence=sib_refs,
                              note="A different model of the same family is named"), set(), siblings  # fmt: skip
    core = [r for r in ev.identity if A.core_in(model, r.text)]
    if core:
        return AttributeCheck(attribute="model", result=CheckResult.UNVERIFIED, critical=True, expected=model,
                              found=sorted({r.text for r in core}), evidence=core,
                              note="Model name found without its generation/variant marker"), set(), siblings  # fmt: skip
    return AttributeCheck(attribute="model", result=CheckResult.UNVERIFIED, critical=True, expected=model,
                          note="Model not found in titles, URL or extracted fields"), set(), siblings  # fmt: skip


def _check(attribute, result, expected, found=(), evidence=(), note=None, critical=True) -> AttributeCheck:
    return AttributeCheck(attribute=attribute, result=result, critical=critical, expected=expected,
                          found=sorted(found), evidence=list(evidence), note=note)  # fmt: skip


def _refs(ev: Evidence, fn) -> list[EvidenceRef]:
    return [r for r in ev.identity if fn(r.text)]


def check_variants(prof: A.ProductProfile, ev: Evidence, model_matched_exactly: bool) -> list[AttributeCheck]:
    text = ev.text
    out: list[AttributeCheck] = []
    M, C, U, NA = CheckResult.MATCH, CheckResult.CONFLICT, CheckResult.UNVERIFIED, CheckResult.NOT_APPLICABLE

    # Handedness. Right-handed is the unstated default; left-handed must be confirmed.
    hand = A.handedness(text)
    refs = _refs(ev, A.handedness)
    if prof.handedness == "left":
        if hand == "left":
            out.append(_check("handedness", M, "left", ["left"], refs))
        elif hand == "right":
            out.append(_check("handedness", C, "left", ["right"], refs))
        elif model_matched_exactly and A.is_code_like(prof.product.manufacturer_model):
            out.append(_check("handedness", M, "left", [], [], "Implied by the exact left-handed model code"))
        else:
            out.append(_check("handedness", U, "left", note="Left-handed not stated in the competitor evidence"))
    else:
        out.append(_check("handedness", C if hand == "left" else M, "right", [hand] if hand else [], refs,
                          None if hand == "left" else "Not indicated as left-handed"))  # fmt: skip

    # Finish / colour (only when the Stompbox product names one).
    if prof.finish:
        comp = A.colour_words(text) - prof.name_words
        # Only the full finish name confirms it: "Bourbon Burst" shares "burst" with
        # "Iced Tea Burst" but is a different finish (-> unverified, not a match).
        if A.mentions_model(prof.finish, text):
            out.append(_check("finish", M, prof.finish, comp, _refs(ev, A.colour_words)))
        elif comp and not comp & prof.finish_colours:
            out.append(_check("finish", C, prof.finish, comp, _refs(ev, A.colour_words), "Different colour/finish"))
        else:
            out.append(_check("finish", U, prof.finish, comp, _refs(ev, A.colour_words), "Finish not confirmed"))
    else:
        out.append(_check("finish", NA, None, note="Stompbox product names no finish"))

    # String count, size, wattage: a stated difference is a conflict; silence only
    # leaves an explicitly stated Stompbox value unverified (models usually encode them).
    for attr, fn, explicit, default in (
        ("strings", A.string_counts, prof.strings, prof.strings_default),
        ("size", A.sizes, prof.sizes, "full"),
        ("wattage", A.wattages, prof.wattages, None),
    ):
        comp = fn(text)
        exp = explicit or ({default} if default else set())
        label = ", ".join(sorted(exp)) or None
        if comp and exp and not comp & exp:
            out.append(_check(attr, C, label, comp, _refs(ev, fn)))
        elif comp and exp:
            out.append(_check(attr, M, label, comp, _refs(ev, fn)))
        elif explicit:
            out.append(_check(attr, U, label, note="Not stated by the competitor"))
        else:
            out.append(_check(attr, NA, label))

    # Configuration (amp head / combo / cabinet / speaker layout).
    cabs, forms = A.cab_configs(text), A.amp_forms(text)
    if cabs and cabs != prof.cab_configs:
        out.append(_check("configuration", C, ", ".join(sorted(prof.cab_configs)) or "no cabinet", cabs,
                          _refs(ev, A.cab_configs)))  # fmt: skip
    elif prof.amp_forms and forms - prof.amp_forms:
        out.append(_check("configuration", C, ", ".join(sorted(prof.amp_forms)), forms, _refs(ev, A.amp_forms)))
    elif prof.cab_configs and not cabs:
        out.append(_check("configuration", U, ", ".join(sorted(prof.cab_configs)), note="Cabinet not confirmed"))
    elif prof.cab_configs or prof.amp_forms:
        out.append(_check("configuration", M, ", ".join(sorted(prof.cab_configs | prof.amp_forms)), cabs | forms))
    else:
        out.append(_check("configuration", NA, None))

    # Bundle / pack offers are not the same product offer.
    bundle = A.is_bundle(text)
    out.append(_check("bundle", C if bundle and not prof.bundle else (M if bundle else NA),
                      "bundle" if prof.bundle else "single product", ["bundle"] if bundle else [],
                      _refs(ev, A.is_bundle), "Bundle/pack offer" if bundle and not prof.bundle else None))  # fmt: skip

    # Generation / version and editions.
    comp_g, st_g = A.generations(text), prof.generations
    g = lambda s: [str(x) for x in sorted(s)]  # noqa: E731
    if comp_g and not comp_g & st_g:
        out.append(_check("generation", C, ", ".join(g(st_g)) or "none", g(comp_g), _refs(ev, A.generations)))
    elif st_g and comp_g & st_g:
        out.append(_check("generation", M, ", ".join(g(st_g)), g(comp_g), _refs(ev, A.generations)))
    elif st_g:
        out.append(_check("generation", U, ", ".join(g(st_g)), note="Generation/version not stated"))
    else:
        out.append(_check("generation", NA, None))
    comp_e, st_e = A.editions(text), prof.editions
    if comp_e - st_e:
        out.append(_check("edition", C, ", ".join(sorted(st_e)) or "standard", comp_e, _refs(ev, A.editions)))
    elif st_e and not st_e <= comp_e:
        out.append(_check("edition", U, ", ".join(sorted(st_e)), comp_e, note="Edition not stated"))
    elif st_e:
        out.append(_check("edition", M, ", ".join(sorted(st_e)), comp_e, _refs(ev, A.editions)))
    else:
        out.append(_check("edition", NA, None))
    return out


def check_condition(ev: Evidence) -> ConditionCheck:
    refs = ev.titles + ev.urls + ([ev.availability] if ev.availability else [])
    hits = [(A.condition_of(r.text), r) for r in refs]
    hits = [(c, r) for c, r in hits if c]
    for wanted in ("USED", "REFURBISHED", "NEW"):
        sel = [r for (name, _), r in hits if name == wanted]
        if sel:
            cond = Condition(wanted)
            ok = cond is Condition.NEW
            return ConditionCheck(condition=cond, comparable_to_new=ok, evidence=sel,
                                  note=None if ok else f"{cond.value.lower()} item: never equivalent to a new product")  # fmt: skip
    return ConditionCheck(condition=Condition.UNKNOWN, comparable_to_new=True,
                          note="Condition not stated; treated as a standard new listing")  # fmt: skip


# --------------------------------------------------------------------------- decision


def decide(
    prof: A.ProductProfile, page: PageCheck, brand: AttributeCheck, model: AttributeCheck, groups: set[str],
    siblings: set[str], variants: list[AttributeCheck], condition: ConditionCheck, ev: Evidence,
) -> tuple[MatchStatus, MatchConfidence, list[str], list[str]]:
    reasons: list[str] = []
    review: list[str] = []
    by = {v.attribute: v for v in variants}

    if page.kind is PageKind.NON_PRODUCT_PAGE:
        return MatchStatus.NO_MATCH, MatchConfidence.LOW, [f"Not a single-product page ({page.basis})"], []
    if brand.result is CheckResult.CONFLICT:
        return MatchStatus.NO_MATCH, MatchConfidence.LOW, [f"Different brand: {brand.found} vs {brand.expected}"], []

    if model.result is CheckResult.MATCH:
        reasons.append(f"Model {model.expected!r} found ({model.note})")
        conflicts = [v for v in variants if v.critical and v.result is CheckResult.CONFLICT]
        if conflicts:
            reasons += [f"Variant conflict on {v.attribute}: expected {v.expected!r}, found {v.found}" for v in conflicts]
            return MatchStatus.SIMILAR_MATCH, MatchConfidence.LOW, reasons, []
        if not condition.comparable_to_new:
            reasons.append(f"Same model but condition is {condition.condition.value}: not equivalent to new")
            return MatchStatus.SIMILAR_MATCH, MatchConfidence.LOW, reasons, []
        review += [f"{v.attribute} not confirmed (expected {v.expected!r})" for v in variants
                   if v.result is CheckResult.UNVERIFIED and v.attribute in CONFIRM_REQUIRED]  # fmt: skip
        if brand.result is not CheckResult.MATCH:
            review.append("Brand not found in the competitor evidence")
        if len(groups) < 2:
            review.append(f"Model confirmed by one evidence group only ({sorted(groups)}); title alone is not enough")
        title_siblings = {s for r in ev.titles for s in A.sibling_models(prof.product.manufacturer_model, r.text)}
        if title_siblings:
            review.append(f"Title also names other models {sorted(title_siblings)}")
        if not page.retrieved:
            review.append("Page was not retrieved; identity rests on the search result only")
        if review:
            return MatchStatus.REVIEW_REQUIRED, MatchConfidence.LOW, reasons, review
        conf = MatchConfidence.HIGH if "field" in groups else MatchConfidence.MEDIUM
        reasons.append("Brand confirmed; no critical variant conflict; condition comparable")
        if page.kind is PageKind.UNKNOWN:
            reasons.append("Page type not classified by LLM 2; identity from page title and URL")
        return MatchStatus.EXACT_MATCH, conf, reasons, []

    same_brand = brand.result is CheckResult.MATCH
    if model.result is CheckResult.CONFLICT:
        if same_brand or siblings:
            return MatchStatus.SIMILAR_MATCH, MatchConfidence.LOW, [
                f"Same brand, different model: {model.found} instead of {model.expected!r}"], []
        return MatchStatus.NO_MATCH, MatchConfidence.LOW, [f"Different model: {model.found}"], []
    if model.found:  # core model without its generation marker
        gen = by.get("generation")
        if gen and gen.result is CheckResult.CONFLICT:
            return MatchStatus.SIMILAR_MATCH, MatchConfidence.LOW, [
                f"Same model line, different generation: {gen.found} vs {gen.expected!r}"], []
        return MatchStatus.REVIEW_REQUIRED, MatchConfidence.LOW, [f"Model line found: {model.found}"], [
            model.note or "Model not confirmed"]
    shared = prof.type_stems & A.type_stems(" ".join(r.text for r in ev.titles)) - A.type_stems(
        f"{prof.product.brand} {prof.product.manufacturer_model}")  # fmt: skip
    # Without a model, "same brand + product type" is only meaningful on a page LLM 2
    # classified as a single-product page; otherwise it is usually a category page.
    if same_brand and shared and page.kind is PageKind.PRODUCT_PAGE:
        return MatchStatus.SIMILAR_MATCH, MatchConfidence.LOW, [
            f"Same brand and product type ({sorted(shared)}); the model {model.expected!r} was not found"], []
    if same_brand and shared:
        why = "Same brand and product type, but the model was not found and the page is not classified as a product page"
    elif same_brand:
        why = "Same brand but the model and product type were not found"
    else:
        why = "Neither the model nor brand + product type match"
    return MatchStatus.NO_MATCH, MatchConfidence.LOW, [why], []


# --------------------------------------------------------------------------- comparison


def compare(p: StompboxProduct, c: CompetitorProduct, status: MatchStatus, cond: ConditionCheck,
            price: PriceValidation) -> PriceComparison:  # fmt: skip
    why: list[str] = []
    if status is not MatchStatus.EXACT_MATCH:
        why.append(f"Match status is {status.value}, not EXACT_MATCH")
    if price.status is not PriceStatus.VALID:
        why.append(f"Price status is {price.status.value}")
    elif price.evidence_status is not EvidenceStatus.VERIFIED:
        why.append(f"Price evidence is {price.evidence_status.value}")
    if not c.competitor.price_benchmark:
        t = c.competitor.company_type.value if c.competitor.company_type else "unknown type"
        why.append(f"{c.competitor.domain} is not a retailer price benchmark ({t})")
    # UK gate: benchmarking needs UK_ELIGIBLE or an explicit benchmark exception, for this very domain.
    gate = c.competitor.uk_gate if c.competitor.uk_gate and c.competitor.uk_gate.domain == c.competitor.domain else None
    if uk_why := benchmark_block_reason(gate, c.competitor.domain):
        why.append(uk_why)
    if not cond.comparable_to_new:
        why.append(f"Condition {cond.condition.value} is not comparable to new")
    if p.displayed_price is None or p.currency != "GBP":
        why.append("Stompbox GBP price missing")
    out = PriceComparison(eligible=not why, reasons=why, stompbox_price=p.displayed_price,
                          stompbox_currency=p.currency, competitor_price=price.price)  # fmt: skip
    if out.eligible:
        out.difference_gbp = (price.price - p.displayed_price).quantize(Decimal("0.01"))
    return out


# --------------------------------------------------------------------------- entry points


def match_candidate(p: StompboxProduct, c: CompetitorProduct, now: datetime, max_age_hours: int,
                    prof: A.ProductProfile | None = None) -> CandidateMatch:  # fmt: skip
    prof = prof or A.profile(p)
    ev = Evidence(c)
    page = check_page(c)
    brand = check_brand(p, ev)
    model, groups, siblings = check_model(p, ev)
    variants = check_variants(prof, ev, model.result is CheckResult.MATCH)
    cond = check_condition(ev)
    status, conf, reasons, review = decide(prof, page, brand, model, groups, siblings, variants, cond, ev)
    price = validate_price(c, now, max_age_hours)
    return CandidateMatch(
        candidate_id=c.candidate_id, stompbox_product_id=p.product_id, competitor=c.competitor,
        product_url=c.product_url, extraction_status=c.extraction_status, match_status=status,
        match_confidence=conf, match_reasons=reasons, review_reasons=review, page=page, brand=brand, model=model,
        variants=variants, condition=cond, price=price, comparison=compare(p, c, status, cond, price),
    )  # fmt: skip


_ORDER = {MatchStatus.EXACT_MATCH: 0, MatchStatus.REVIEW_REQUIRED: 1, MatchStatus.SIMILAR_MATCH: 2,
          MatchStatus.NO_MATCH: 3}  # fmt: skip


def evaluate_product(
    pr: ProductIntelligence, p: StompboxProduct, max_age_hours: int, now: datetime
) -> ProductMatchSummary:
    """Match every candidate of one product's Phase 5 result (used per product by the pipeline)."""
    prof = A.profile(p)
    matches = [match_candidate(p, c, now, max_age_hours, prof) for c in pr.candidates]
    matches.sort(key=lambda m: (_ORDER[m.match_status], not m.comparison.eligible, m.competitor.domain))
    return ProductMatchSummary(
        product_id=p.product_id, product_name=p.product_name, brand=p.brand,
        manufacturer_model=p.manufacturer_model, stompbox_price=p.displayed_price,
        counts={s: sum(m.match_status is s for m in matches) for s in MatchStatus},
        comparable_prices=sum(m.comparison.eligible for m in matches), candidates=matches,
    )  # fmt: skip


def evaluate_run(
    run: IntelligenceRunResult, products: dict[str, StompboxProduct], max_age_hours: int,
    now: datetime | None = None,
) -> MatchingRunResult:
    """Match every candidate of a Phase 5 run. Raises KeyError for unknown product ids."""
    now = now or datetime.now(UTC)
    warnings: list[str] = []
    if run.mode.value == "LLM_DISABLED":
        warnings.append("Source run had no LLM extraction: identity rests on page titles and URLs, every price is "
                        "NOT_EXTRACTED, and no candidate qualifies for price comparison")  # fmt: skip
    out = [evaluate_product(pr, products[pr.product.product_id], max_age_hours, now) for pr in run.products]
    return MatchingRunResult(source_run_id=run.run_id, source_mode=run.mode, evaluated_at=now,
                             price_max_age_hours=max_age_hours, products=out, warnings=warnings)  # fmt: skip
