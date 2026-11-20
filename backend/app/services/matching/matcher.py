"""Product matching (deterministic; Phase 6, extended for the Product Intelligence Agent).

For each Stompbox product and Phase 5 candidate:
  1. Gather identity evidence in three independent groups: titles (retrieved
     page title, search title, extracted title), URL paths, and extracted
     model/SKU fields. Extracted fields exist only when LLM 2 ran and their
     quotes were verified in Phase 5. Search snippets are never used.
  2. Check page kind, brand, model (incl. generation and sibling models),
     critical variants and condition.
  3. Decide EXACT_MATCH / EQUIVALENT_VARIANT / SIMILAR_MATCH / REVIEW_REQUIRED /
     NO_MATCH by fixed rules (see `decide`). Title similarity alone never yields
     EXACT_MATCH: the exact model identifier must be found in at least two
     evidence groups. EQUIVALENT_VARIANT is granted by Python alone: otherwise
     the same product, only the cosmetic finish/colour differs (decision U-1).
  4. Apply LLM 2's verified evidence conservatively (`reconcile`): a verified
     ambiguity/conflict issue, or a Task A proposal that disagrees with an
     EXACT_MATCH / EQUIVALENT_VARIANT decision, can only turn it into
     REVIEW_REQUIRED. LLM 2 never upgrades, downgrades or replaces a
     classification (decisions D-26, D-34).
  5. Validate the price separately (`price.validate_price`), decide whether the
     pair qualifies for direct UK price comparison (EXACT_MATCH only), and
     whether an equivalent-variant / similar price is shown as an
     alternative-product price (never benchmarked, no difference).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlsplit

from app.agents.intelligence.schemas import (
    PROPOSAL_STATUS,
    CompetitorProduct,
    ExtractionStatus,
    IntelligenceRunResult,
    IssueKind,
    PageType,
    ProductIntelligence,
    ProposalStatus,
    SpecCategory,
)
from app.schemas.catalogue import StompboxProduct
from app.schemas.common import DomainApprovalStatus, EvidenceStatus, MatchStatus
from app.schemas.search import RetrievalStatus
from app.services.matching import attributes as A
from app.services.matching.price import validate_price
from app.services.matching.view import build_view
from app.services.specs import ELECTRONICS_CATEGORIES, is_meaningful_electronics, spec_category
from app.services.matching.schemas import (
    AlternativePrice,
    AttributeCheck,
    AvailabilityView,
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
    PriceKind,
    PriceStatus,
    PriceValidation,
    ProductMatchSummary,
    ProposalAgreement,
)
from app.services.text_match import url_text

MAX_REF_CHARS = 200
# An UNVERIFIED result on these blocks EXACT_MATCH (the Stompbox product defines
# them and listings normally state them). Others only block on CONFLICT.
CONFIRM_REQUIRED = {"handedness", "finish", "generation", "edition", "configuration",
                    "pickups", "electronics", "electro_acoustic", "designation"}  # fmt: skip
# Only specifications categorised as electronics or pickup configuration take part in the
# electronics rules (Stage 6.1). Controls, power supply and output power never do.
# The only attributes whose conflict alone allows EQUIVALENT_VARIANT (approved decision U-1).
COSMETIC_ATTRIBUTES = {"finish"}
# Verified LLM 2 issues of these kinds on these fields add uncertainty to an identity decision.
_IDENTITY_ISSUE_KINDS = {IssueKind.AMBIGUOUS_VARIANT, IssueKind.CONFLICTING_INFORMATION}
_IDENTITY_FIELDS = {"product_title", "title", "brand", "model", "sku", "variant", "finish", "colour", "color",
                    "handedness", "size", "configuration", "specifications", "edition", "generation", "strings",
                    "pickups", "electronics", "condition"}  # fmt: skip
# Strictness order for comparing a proposal with Python's decision (REVIEW_REQUIRED is uncertainty).
_RANK = {MatchStatus.EXACT_MATCH: 0, MatchStatus.EQUIVALENT_VARIANT: 1, MatchStatus.SIMILAR_MATCH: 2,
         MatchStatus.NO_MATCH: 3}  # fmt: skip
_CONFIDENT = (MatchStatus.EXACT_MATCH, MatchStatus.EQUIVALENT_VARIANT)

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
        # Electronics specifications (verified against the page in Task B). Kept out of `identity`:
        # they never count as model evidence, only as electronics evidence.
        # The category is always derived here from name and value (Stage 6.1.1): a stored or
        # client-submitted category is never trusted. A disagreeing stored value is kept for audit.
        cats = [(s, spec_category(s.name, s.value)) for s in c.specifications]
        self.category_mismatches = [
            f"Specification {s.name!r}: stored category {s.category.value!r} ignored; derived {cat.value!r}"
            for s, cat in cats if s.category is not None and s.category is not cat]  # fmt: skip
        elec = [(s, cat) for s, cat in cats if cat in ELECTRONICS_CATEGORIES]
        self.specs = [_ref(EvidenceSource.EXTRACTED_SPEC, f"{s.name}: {s.value}") for s, _ in elec]
        self.pickup_values = [s.value for s, cat in elec if cat is SpecCategory.PICKUP_CONFIGURATION]
        self.electronics_values = [s.value for s, cat in elec if cat is SpecCategory.ELECTRONICS]

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
        if A.finish_in(prof.finish, text):  # catalogue finish or a controlled alias
            # Stage 6.1.1: the finish named together with another finish ("Ice Tea / Ebony") is mixed
            # evidence, never a match. Not a conflict either: the page may offer both.
            extra = A.other_finish_colours(prof.finish, text, prof.name_words)
            if extra:
                out.append(_check("finish", U, prof.finish, comp, _refs(ev, A.colour_words),
                                  f"Mixed finish evidence: {prof.finish!r} named together with {sorted(extra)}"))  # fmt: skip
            else:
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
    if prof.designation:
        out.append(check_designation(prof.designation, ev))
    out += check_electronics(prof, ev, model_matched_exactly)
    return out


def check_designation(d: A.ApprovedDesignation, ev: Evidence) -> AttributeCheck:
    """Approved model designation (e.g. SB-028 "Figured"): a model attribute, never cosmetic.

    Confirmed by the designation as a whole word in a title, the URL or the extracted model/SKU, or
    by a listed SKU of the designated model. A listed SKU of the undesignated model is a conflict.
    Silence is unverified: a page that does not name the designation (a plain-top listing, say)
    is never accepted as the designated model. Specifications are not identity evidence."""
    named = [r for r in ev.identity if A.designation_in(d.designation, r.text)]
    own = [r for r in ev.identity if A.sku_in(d.model_skus, r.text)]
    other = [r for r in ev.identity if A.sku_in(d.other_model_skus, r.text)]
    other_skus = set().union(*(A.sku_in(d.other_model_skus, r.text) for r in other)) if other else set()
    exp = d.designation
    if other and (named or own):
        return _check("designation", CheckResult.UNVERIFIED, exp, [d.designation, *other_skus], [*named, *own, *other],
                      f"Contradictory: {d.designation!r} named together with the undesignated model's SKU "
                      f"{sorted(other_skus)}")  # fmt: skip
    if other:
        return _check("designation", CheckResult.CONFLICT, exp, other_skus, other,
                      f"SKU of the model without {d.designation!r} ({d.decided})")  # fmt: skip
    if named or own:
        return _check("designation", CheckResult.MATCH, exp, [d.designation], [*named, *own],
                      f"Approved identity: {d.designation} model ({d.decided})")  # fmt: skip
    return _check("designation", CheckResult.UNVERIFIED, exp,
                  note=f"{d.designation!r} not stated: the model without it cannot be ruled out ({d.decided})")


def check_electronics(prof: A.ProductProfile, ev: Evidence, model_matched_exactly: bool) -> list[AttributeCheck]:
    """Pickup layout, active/passive electronics and electro-acoustic (audit fix).

    Compared against verified electronics specifications and title/URL/extracted-field evidence.
    A stated difference is a CONFLICT (-> SIMILAR_MATCH). The catalogue value stated but not shown
    by the competitor, or a variant token in the competitor's title that the catalogue does not
    state, is UNVERIFIED (-> REVIEW_REQUIRED). Nothing is inferred from silence otherwise.
    """
    M, C, U, NA = CheckResult.MATCH, CheckResult.CONFLICT, CheckResult.UNVERIFIED, CheckResult.NOT_APPLICABLE
    ident = ev.identity
    model = prof.product.manufacturer_model
    implied = model_matched_exactly and A.is_code_like(model)
    out: list[AttributeCheck] = []

    def compare(attr: str, known: set[str], ident_fn, spec_fn, implied_by_model: bool,
                spec_values: list[str]) -> AttributeCheck:  # fmt: skip
        title_vals = set().union(*(ident_fn(r.text) for r in ident)) if ident else set()
        spec_vals = set().union(*(spec_fn(v) for v in spec_values)) if spec_values else set()
        found = title_vals | spec_vals
        refs = [r for r in ident if ident_fn(r.text)] + [r for r in ev.specs if spec_fn(r.text)]
        label = ", ".join(sorted(known)) or None
        if known:
            if found:
                ok = found & known and not (found - known)
                return _check(attr, M if ok else C, label, found, refs, None if ok else f"Different {attr}")
            if implied_by_model:
                return _check(attr, M, label, [], [], "Implied by the exact model, which states it")
            return _check(attr, U, label, note=f"{attr.capitalize()} not stated by the competitor")
        if title_vals:
            return _check(attr, U, "not stated in the catalogue", title_vals, [r for r in ident if ident_fn(r.text)],
                          f"The competitor title names {attr} {sorted(title_vals)}; the catalogue product states none")  # fmt: skip
        return _check(attr, NA, None)

    out.append(compare("pickups", prof.pickups, A.pickup_configs, A.pickup_configs,
                       model_matched_exactly and prof.pickups <= A.pickup_configs(model), ev.pickup_values))  # fmt: skip
    out.append(compare("electronics", prof.electronics, A.electronics_modes,
                       lambda t: A.electronics_modes(t, bare=True),
                       model_matched_exactly and prof.electronics <= A.electronics_modes(model),
                       ev.electronics_values + ev.pickup_values))  # fmt: skip

    # Electro-acoustic: from the catalogue subcategory/name; competitor evidence must say so explicitly.
    states = set().union(*(A.electro_states(r.text) for r in ident)) if ident else set()
    # Only ELECTRONICS-category specs (electronics, preamp, pickup system, pickups) speak to this;
    # controls and power-supply specs never confirm a preamp. An explicit "None" contradicts it; a
    # value confirms it only when it names an actual device, type or maker (Stage 6.1.1), so a
    # generic "Electronics: Volume, Tone" stays unknown.
    for v in ev.electronics_values:
        if A.is_none_value(v):
            states.add("none")
        elif is_meaningful_electronics(v):
            states.add("electro")
    refs = [r for r in ident if A.electro_states(r.text)] + ev.specs
    if prof.electro is True:
        if "none" in states:
            out.append(_check("electro_acoustic", C, "electro-acoustic", sorted(states), refs,
                              "The competitor states no electronics"))  # fmt: skip
        elif "electro" in states:
            out.append(_check("electro_acoustic", M, "electro-acoustic", ["electro"], refs))
        elif implied:
            out.append(_check("electro_acoustic", M, "electro-acoustic", [], [], "Implied by the exact model code"))
        else:
            out.append(_check("electro_acoustic", U, "electro-acoustic",
                              note="Electronics not confirmed by the competitor (acoustic-only versions may exist)"))  # fmt: skip
    elif prof.electro is False:
        if "electro" in states and "none" not in states:
            out.append(_check("electro_acoustic", C, "acoustic (no electronics)", ["electro"], refs,
                              "The competitor lists an electro-acoustic version"))  # fmt: skip
        else:
            out.append(_check("electro_acoustic", NA, "acoustic (no electronics)"))
    else:
        out.append(_check("electro_acoustic", NA, None))
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
        # U-1: a cosmetic finish/colour difference alone can be an equivalent variant; any other
        # difference (handedness, size, configuration, strings, wattage, generation...) cannot.
        finish_only = bool(conflicts) and all(v.attribute in COSMETIC_ATTRIBUTES for v in conflicts)
        if conflicts:
            reasons += [f"Variant conflict on {v.attribute}: expected {v.expected!r}, found {v.found}" for v in conflicts]
            if not finish_only:
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
            if finish_only:
                review.append("Only the finish differs, but the rest of the identity is not confirmed: "
                              "equivalent variant not established")  # fmt: skip
            return MatchStatus.REVIEW_REQUIRED, MatchConfidence.LOW, reasons, review
        conf = MatchConfidence.HIGH if "field" in groups else MatchConfidence.MEDIUM
        if page.kind is PageKind.UNKNOWN:
            reasons.append("Page type not classified by LLM 2; identity from page title and URL")
        if finish_only:
            reasons.append("Otherwise the same product: brand and model confirmed, no other variant conflict, "
                           "condition comparable; only the cosmetic finish/colour differs")  # fmt: skip
            return MatchStatus.EQUIVALENT_VARIANT, conf, reasons, []
        reasons.append("Brand confirmed; no critical variant conflict; condition comparable")
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


# --------------------------------------------------------------------------- LLM 2 evidence (conservative only)


def _quoted(text: str, quote: str | None) -> str:
    return f"{text} (page: {quote!r})" if quote else text


def reconcile(status: MatchStatus, conf: MatchConfidence, review: list[str],
              c: CompetitorProduct) -> tuple[MatchStatus, MatchConfidence, list[str], ProposalAgreement]:  # fmt: skip
    """Apply LLM 2's verified issues and Task A proposal after Python's decision.

    The only possible effect is EXACT_MATCH / EQUIVALENT_VARIANT -> REVIEW_REQUIRED
    (decision U-4). Unverified issues and UNSUPPORTED proposals are ignored.
    """
    review = list(review)
    added: list[str] = []
    for i in c.extraction_issues:
        if i.verified and i.kind in _IDENTITY_ISSUE_KINDS and set(i.fields) & _IDENTITY_FIELDS:
            added.append(_quoted(f"LLM 2 flagged {i.kind.value.lower().replace('_', ' ')} on "
                                 f"{', '.join(i.fields)}: {i.description}", i.quotes[0] if i.quotes else None))  # fmt: skip
    proposal = c.match_proposal if c.match_proposal_status is ProposalStatus.COMPLETED else None
    agreement = ProposalAgreement.NOT_AVAILABLE
    if proposal is not None:
        proposed = PROPOSAL_STATUS[proposal.classification]
        if status is MatchStatus.REVIEW_REQUIRED:
            agreement = ProposalAgreement.PYTHON_STRICTER if proposed in _CONFIDENT else ProposalAgreement.LLM_STRICTER
        elif proposed is status:
            agreement = ProposalAgreement.AGREES
        else:
            agreement = (ProposalAgreement.LLM_STRICTER if _RANK[proposed] > _RANK[status]
                         else ProposalAgreement.PYTHON_STRICTER)  # fmt: skip
        if status in _CONFIDENT:
            if agreement is ProposalAgreement.LLM_STRICTER:
                why = next((r for r in proposal.reasons if r.verified), None)
                added.append(_quoted(f"LLM 2 proposed {proposal.classification.value}"
                                     + (f": {why.text}" if why else ""), why.quote if why else None))  # fmt: skip
            for d in proposal.differing_attributes:
                if d.verified and not d.cosmetic:
                    added.append(_quoted(f"LLM 2 found a non-cosmetic difference in {d.attribute}: "
                                         f"{d.page_value!r} vs {d.catalogue_value!r}", d.quote))  # fmt: skip
    if added and status in _CONFIDENT:
        return MatchStatus.REVIEW_REQUIRED, MatchConfidence.LOW, review + added, agreement
    return status, conf, review, agreement


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
        why.append(f"{c.competitor.domain} is not marked as a price benchmark ({t})")
    if not cond.comparable_to_new:
        why.append(f"Condition {cond.condition.value} is not comparable to new")
    if p.displayed_price is None or p.currency != "GBP":
        why.append("Stompbox GBP price missing")
    out = PriceComparison(eligible=not why, reasons=why, stompbox_price=p.displayed_price,
                          stompbox_currency=p.currency, competitor_price=price.price)  # fmt: skip
    if out.eligible:
        out.difference_gbp = (price.price - p.displayed_price).quantize(Decimal("0.01"))
    return out


_SEARCH_APPROVED = {DomainApprovalStatus.PRECONFIGURED, DomainApprovalStatus.APPROVED, DomainApprovalStatus.AUTO_APPROVED}
_CONDITION_LABEL = {Condition.USED: "Used", Condition.REFURBISHED: "Refurbished / B-stock",
                    Condition.NEW: "New", Condition.UNKNOWN: "Condition not stated"}  # fmt: skip


def alternative_price(c: CompetitorProduct, status: MatchStatus, cond: ConditionCheck,
                      price: PriceValidation) -> AlternativePrice | None:  # fmt: skip
    """Alternative-product price for EQUIVALENT_VARIANT / SIMILAR_MATCH (decisions D-28, D-32).

    Same price validation as a direct price; no benchmark flag needed, but the domain must be
    approved for product search; used/refurbished shown with an explicit label; no difference.
    """
    if status not in (MatchStatus.EQUIVALENT_VARIANT, MatchStatus.SIMILAR_MATCH):
        return None
    why: list[str] = []
    if price.status is not PriceStatus.VALID:
        why.append(f"Price status is {price.status.value}")
    elif price.evidence_status is not EvidenceStatus.VERIFIED:
        why.append(f"Price evidence is {price.evidence_status.value}")
    if c.competitor.approval_status not in _SEARCH_APPROVED:
        why.append(f"{c.competitor.domain} is not approved for product search")
    shown = not why
    return AlternativePrice(
        shown=shown, match_status=status, reasons=why, price=price.price if shown else None,
        currency=price.currency if shown else None, price_text=price.price_text if shown else None,
        condition=cond.condition, condition_label=_CONDITION_LABEL[cond.condition],
    )  # fmt: skip


def availability_view(c: CompetitorProduct) -> AvailabilityView:
    ev = next((e for e in c.field_evidence if e.field.value == "availability"), None)
    reason = c.availability_reason
    if reason is None:
        reason = ("No LLM extraction: availability not read from the page"
                  if c.extraction_status is not ExtractionStatus.COMPLETED else "No verified availability text")  # fmt: skip
    return AvailabilityView(status=c.availability_status, text=c.availability, quote=ev.quote if ev and c.availability
                            else None, reason=reason, proposed=c.availability_proposed)  # fmt: skip


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
    status, conf, review, agreement = reconcile(status, conf, review, c)
    price = validate_price(c, now, max_age_hours)
    comparison = compare(p, c, status, cond, price)
    alt = alternative_price(c, status, cond, price)
    kind = (PriceKind.DIRECT if comparison.eligible else
            PriceKind.ALTERNATIVE if alt is not None and alt.shown else PriceKind.NONE)  # fmt: skip
    proposal_ok = c.match_proposal is not None and c.match_proposal_status is ProposalStatus.COMPLETED
    m = CandidateMatch(
        candidate_id=c.candidate_id, stompbox_product_id=p.product_id, competitor=c.competitor,
        product_url=c.product_url, extraction_status=c.extraction_status, match_status=status,
        match_confidence=conf, match_reasons=reasons, review_reasons=review, page=page, brand=brand, model=model,
        variants=variants, condition=cond, price=price, comparison=comparison,
        proposed_match=c.match_proposal.classification if proposal_ok else None,
        proposal_status=c.match_proposal_status, proposal_agreement=agreement, alternative_price=alt,
        price_kind=kind, availability=availability_view(c), spec_category_mismatches=ev.category_mismatches,
    )  # fmt: skip
    m.intelligence = build_view(m, c)
    return m


_ORDER = {MatchStatus.EXACT_MATCH: 0, MatchStatus.EQUIVALENT_VARIANT: 1, MatchStatus.REVIEW_REQUIRED: 2,
          MatchStatus.SIMILAR_MATCH: 3, MatchStatus.NO_MATCH: 4}  # fmt: skip


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
        comparable_prices=sum(m.comparison.eligible for m in matches),
        alternative_prices=sum(m.price_kind is PriceKind.ALTERNATIVE for m in matches), candidates=matches,
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
