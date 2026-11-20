"""Deterministic checks on LLM 2 output.

Task B (extraction). LLM 2 proposes values with verbatim quotes. A value is
kept only if:
- its quote appears in the page text LLM 2 was given (or the page title), and
- the value itself appears in that quote.

The price gets extra checks:
- the amount is parsed in Python and must be one of the amounts in its quote;
- an amount shown as was/RRP/original/saving is excluded;
- a conditional amount ("from", per month, deposit) is ambiguous;
- the currency must be shown explicitly;
- zero or unparseable amounts are invalid;
- several verified current prices that cannot be tied to one product/variant
  make the price AMBIGUOUS. No price is ever selected automatically (P-6).

Per-option prices, options and issues are verified the same way; an issue
whose quotes are not all in the page never affects anything. Availability is
normalised deterministically (`app.services.availability`).

Task A (match proposal). Each reason and differing attribute must quote the
page verbatim. A proposal with no verified quote is UNSUPPORTED and ignored.
Whether a differing attribute is cosmetic is decided here from its name, never
by LLM 2.

None of this compares prices or decides whether the page is the same product
as the Stompbox item (that is the matcher).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.agents.intelligence.schemas import (
    PROPOSAL_STATUS,
    ExtractedField,
    FieldEvidence,
    FieldStatus,
    IgnoredPriceCategory,
    IgnoredPriceRecord,
    IssueKind,
    IssueRecord,
    MatchProposal,
    MatchProposalRecord,
    NormalizedSpec,
    OptionPriceEvidence,
    PageExtraction,
    PageType,
    ProposalStatus,
    SpecCategory,
    VerifiedDifference,
    VerifiedOption,
    VerifiedReason,
)
from app.agents.intelligence.variants import OptionCandidate, associate
from app.schemas.catalogue import StompboxProduct
from app.schemas.common import AvailabilityStatus
from app.services.availability import normalize_availability
from app.services.matching.attributes import ProductProfile, profile
from app.services.specs import spec_category
from app.services.prices import (
    find_prices,
    ignored_price_category,
    is_delivery_context,
    is_delivery_discount,
    is_conditional_context,
    is_sale_context,
    mentions_sale,
    parse_single_price,
)
from app.services.text_match import mentions_model

MAX_SPECS = 15
MAX_TEXT = 300
MAX_OPTIONS = 10
MAX_OPTION_PRICES = 10
MAX_ISSUES = 10
MAX_REASONS = 8


def norm(text: str | None) -> str:
    """Comparison form: casefold, unify quotes/dashes, collapse whitespace."""
    t = (text or "").translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"}))
    return " ".join(t.split()).casefold()


@dataclass
class CheckedExtraction:
    values: dict[ExtractedField, str | None] = field(default_factory=dict)
    price: Decimal | None = None
    currency: str | None = None
    price_text: str | None = None
    specifications: list[NormalizedSpec] = field(default_factory=list)
    sale_indicated: bool = False
    ignored_price_details: list[IgnoredPriceRecord] = field(default_factory=list)
    evidence: list[FieldEvidence] = field(default_factory=list)
    options: list[VerifiedOption] = field(default_factory=list)
    option_prices: list[OptionPriceEvidence] = field(default_factory=list)
    issues: list[IssueRecord] = field(default_factory=list)
    availability_status: AvailabilityStatus = AvailabilityStatus.UNKNOWN
    availability_reason: str | None = None


def _quote_in_page(quote: str | None, page: str) -> bool:
    q = norm(quote)
    return bool(q) and q in page


def _check_text_field(
    f: ExtractedField, value: str | None, quote: str | None, page: str, flexible: bool = False
) -> FieldEvidence:
    value = (value or "").strip()[:MAX_TEXT] or None
    quote = (quote or "").strip()[:MAX_TEXT * 2] or None
    if value is None:
        return FieldEvidence(field=f, status=FieldStatus.NOT_FOUND, value=None, quote=quote)
    if not _quote_in_page(quote, page):
        return FieldEvidence(field=f, status=FieldStatus.UNSUPPORTED, value=value, quote=quote,
                             note="quote not found in the retrieved page text")  # fmt: skip
    in_quote = mentions_model(value, quote) if flexible else norm(value) in norm(quote)
    if not in_quote:
        return FieldEvidence(field=f, status=FieldStatus.UNSUPPORTED, value=value, quote=quote,
                             note="value does not appear in its quote")  # fmt: skip
    return FieldEvidence(field=f, status=FieldStatus.EXTRACTED, value=value, quote=quote)


@dataclass
class _Amount:
    status: FieldStatus
    note: str | None = None
    amount: Decimal | None = None
    currency: str | None = None


def _verify_amount(value: str | None, quote: str | None, page: str,
                   ignored: dict[Decimal, set[IgnoredPriceCategory]], conditional: bool,
                   delivery: set[Decimal] = frozenset()) -> _Amount:  # fmt: skip
    """The shared price checks, in order. EXTRACTED carries the amount and currency.

    `ignored`: amounts LLM 2 listed as ignored prices, with the deterministic category of each
    ignored text (sale / finance / other); `delivery`: amounts it listed as delivery charges. A
    delivery charge never becomes a product price.

    An amount given as the current price AND listed as ignored is never used, but the reason must
    match the evidence (post-Stage 7): EXCLUDED_SALE_PRICE only when the ignored text itself carries
    was/RRP/original/saving wording; AMBIGUOUS for a finance/conditional text; otherwise (a bare
    amount, category "other") CONFLICT: LLM 2 contradicted itself and nothing on the page says which
    reading is right.
    """
    if value is None:
        return _Amount(FieldStatus.NOT_FOUND)
    parsed = parse_single_price(value)
    if parsed is None:
        return _Amount(FieldStatus.INVALID, "price is not a single amount with a currency marker")
    if parsed.amount <= 0:
        return _Amount(FieldStatus.INVALID, "non-positive amount (a missing price is never treated as zero)")
    if not _quote_in_page(quote, page):
        return _Amount(FieldStatus.UNSUPPORTED, "price quote not found in the retrieved page text")
    in_quote = [m for m in find_prices(quote) if m.amount == parsed.amount]
    if not in_quote:
        return _Amount(FieldStatus.UNSUPPORTED, "amount does not appear in its quote")
    if all(is_delivery_context(quote, m) for m in in_quote):
        return _Amount(FieldStatus.INVALID, "amount is a delivery/shipping charge, not a product price")
    if all(is_sale_context(quote, m) for m in in_quote):
        return _Amount(FieldStatus.EXCLUDED_SALE_PRICE, "amount is shown as a was/RRP/original/saving figure")
    cats = ignored.get(parsed.amount, set())
    if IgnoredPriceCategory.SALE in cats:
        return _Amount(FieldStatus.EXCLUDED_SALE_PRICE,
                       "LLM 2 also listed this amount as an ignored price with was/RRP/original/saving wording")  # fmt: skip
    if IgnoredPriceCategory.FINANCE in cats:
        return _Amount(FieldStatus.AMBIGUOUS,
                       "LLM 2 also listed this amount as an ignored finance/conditional price")  # fmt: skip
    if cats:
        return _Amount(FieldStatus.CONFLICT,
                       "LLM 2 gave this amount as the current price and also listed it as an ignored price, with no "
                       "was/RRP, finance or delivery wording to say why; not used (the page evidence does not settle "
                       "whether it is the current price)")  # fmt: skip
    if parsed.amount in delivery:
        return _Amount(FieldStatus.AMBIGUOUS, "the same amount is listed as a delivery charge on the page")
    if conditional or any(is_conditional_context(quote, m) for m in in_quote):
        return _Amount(FieldStatus.AMBIGUOUS, "conditional price (from / per month / deposit / option-dependent)")
    shown = {m.currency for m in in_quote} | {parsed.currency}
    shown.discard(None)
    if not shown:
        return _Amount(FieldStatus.AMBIGUOUS, "no explicit currency shown with the amount")
    if len(shown) > 1:
        return _Amount(FieldStatus.CONFLICT, f"different currencies shown: {sorted(shown)}")
    return _Amount(FieldStatus.EXTRACTED, amount=parsed.amount, currency=shown.pop())


def ignored_price_details(ext: PageExtraction) -> list[IgnoredPriceRecord]:
    """LLM 2's ignored prices with a deterministic category (kept for audit)."""
    return [IgnoredPriceRecord(text=t.strip()[:200], category=IgnoredPriceCategory(ignored_price_category(t).upper()))
            for t in ext.ignored_prices[:10] if t.strip()]  # fmt: skip


def _ignored_amounts(ext: PageExtraction) -> dict[Decimal, set[IgnoredPriceCategory]]:
    """Amounts LLM 2 ignored as was/RRP/finance/other prices (delivery charges excluded), each with the
    categories of the ignored texts it appears in."""
    out: dict[Decimal, set[IgnoredPriceCategory]] = {}
    for r in ignored_price_details(ext):
        if r.category is not IgnoredPriceCategory.DELIVERY:
            for m in find_prices(r.text):
                out.setdefault(m.amount, set()).add(r.category)
    return out


def _delivery_amounts(ext: PageExtraction) -> set[Decimal]:
    return {m.amount for r in ignored_price_details(ext) if r.category is IgnoredPriceCategory.DELIVERY
            for m in find_prices(r.text)}  # fmt: skip


def _check_price(ext: PageExtraction, page: str) -> tuple[FieldEvidence, Decimal | None, str | None]:
    f = ExtractedField.PRICE
    value = (ext.displayed_price or "").strip() or None
    quote = (ext.price_quote or "").strip()[:MAX_TEXT * 2] or None

    def ev(status: FieldStatus, note: str | None = None) -> tuple[FieldEvidence, None, None]:
        return FieldEvidence(field=f, status=status, value=value, quote=quote, note=note), None, None

    r = _verify_amount(value, quote, page, _ignored_amounts(ext), ext.price_is_conditional, _delivery_amounts(ext))
    if r.status is not FieldStatus.EXTRACTED:
        return ev(r.status, r.note)
    stated = (ext.currency or "").strip().upper() or None
    if stated and stated != r.currency:
        return ev(FieldStatus.CONFLICT, f"stated currency {stated} but the page shows {r.currency}")
    if ext.page_type is not PageType.PRODUCT_PAGE:
        return ev(FieldStatus.AMBIGUOUS, f"page is a {ext.page_type.value}, not a single-product page")
    return FieldEvidence(field=f, status=FieldStatus.EXTRACTED, value=value, quote=quote), r.amount, r.currency


def _check_option_prices(ext: PageExtraction, page: str) -> list[OptionPriceEvidence]:
    ignored, delivery = _ignored_amounts(ext), _delivery_amounts(ext)
    out = []
    for op in ext.option_prices[:MAX_OPTION_PRICES]:
        value = (op.displayed_price or "").strip()[:60] or None
        quote = (op.price_quote or "").strip()[:MAX_TEXT * 2] or None
        r = _verify_amount(value, quote, page, ignored, False, delivery)
        out.append(OptionPriceEvidence(option=op.option.strip()[:120], status=r.status, price_text=value,
                                       amount=r.amount, currency=r.currency, quote=quote, note=r.note))  # fmt: skip
    return out


# Explicit selection wording next to the value. A bare "Colour: Black" line is NOT selection evidence:
# retrieved pages use the same form for specification bullets ("- Colour: Ocean Blue Grey") and
# hardware finishes ("Finish: Chrome"), so it cannot tell a selected swatch from a spec.
_OPTION_WORD = r"(?:\s+(?:colou?r|finish|option|size|variant|handedness|model|version))?"
# "Selected: Black", "Selected colour - Black", "Chosen finish = Black"; a bare "selected black" (prose such as
# "carefully selected black spruce") needs a separator. "You have selected" / "currently selected" are explicit.
_SELECT_BEFORE = (r"(?:(?:selected|chosen|selection)" + _OPTION_WORD + r"\s*[:\-=]\s*"
                  r"|(?:you\s+have\s+selected|currently\s+selected)" + _OPTION_WORD + r"\s*[:\-=]?\s*)")  # fmt: skip
_SELECT_AFTER = r"\s*(?:[(\[]\s*(?:currently\s+)?(?:selected|chosen|current)\s*[)\]]|[-–:]\s*(?:selected|chosen)\b)"
_CHECKMARK = r"(?:[✓✔☑]|\[x\])"


# The marked text must be the whole value: "Selected: Black Sunburst" does not select "Black", and
# "Jet Black (selected)" does not select "Black". It starts at the quote start or a delimiter, and ends
# at the quote end or a delimiter.
_VALUE_START = r"(?:^|(?<=[,;/|(\[:✓✔☑\]])|(?<=[,;/|(\[:✓✔☑\]]\s))"
_VALUE_END = r"(?=\s*(?:$|[,;/|)\].]))"


def selection_evidence(value: str, quote: str) -> str | None:
    """The quote text that explicitly marks `value` (the whole value) as selected, or None."""
    v = re.escape(norm(value))
    q = norm(quote)
    for pattern in (rf"\b{_SELECT_BEFORE}{v}{_VALUE_END}", rf"{_VALUE_START}\s*{v}{_SELECT_AFTER}",
                    rf"{_CHECKMARK}\s*{v}{_VALUE_END}", rf"{_VALUE_START}\s*{v}\s*{_CHECKMARK}"):  # fmt: skip
        m = re.search(pattern, q)
        if m:
            return m.group(0)
    return None


def _check_options(ext: PageExtraction, page: str) -> list[VerifiedOption]:
    """Keep options whose quote and every value are in the page text.

    A selected value is kept only with explicit selection evidence in the verified quote
    (`selection_evidence`); otherwise the option is kept but nothing is treated as selected.
    """
    out = []
    for o in ext.options[:MAX_OPTIONS]:
        values = [v.strip()[:120] for v in o.values if v.strip()]
        if not values or not _quote_in_page(o.quote, page) or not all(norm(v) in page for v in values):
            continue
        claimed = (o.selected_value or "").strip() or None
        if claimed and norm(claimed) not in {norm(v) for v in values}:
            claimed = None
        evidence = selection_evidence(claimed, o.quote) if claimed else None
        out.append(VerifiedOption(name=o.name.strip()[:80], values=values[:20],
                                  selected_value=claimed if evidence else None, claimed_selected_value=claimed,
                                  selection_evidence=evidence, quote=o.quote.strip()[:MAX_TEXT * 2]))  # fmt: skip
    return out


def _check_issues(ext: PageExtraction, page: str) -> list[IssueRecord]:
    out = []
    for i in ext.issues[:MAX_ISSUES]:
        quotes = [q.strip()[:MAX_TEXT * 2] for q in i.quotes if q.strip()][:5]
        out.append(IssueRecord(kind=i.kind, fields=[f.strip().casefold()[:40] for f in i.fields if f.strip()][:10],
                               description=i.description.strip()[:300], quotes=quotes,
                               verified=bool(quotes) and all(_quote_in_page(q, page) for q in quotes)))  # fmt: skip
    return out


def _current_amounts(text: str, excluded: frozenset[Decimal] | set[Decimal] = frozenset()) -> set[Decimal]:
    """Amounts in the text that could be a current product price. Never: was/RRP/saving amounts,
    conditional or finance amounts, delivery/shipping/postage/collection charges or delivery
    discounts (Stage 6.1.1), or amounts LLM 2 listed as delivery or finance charges (`excluded`)."""
    return {m.amount for m in find_prices(text)
            if not is_sale_context(text, m) and not is_conditional_context(text, m)
            and not is_delivery_context(text, m) and not is_delivery_discount(text, m)
            and m.amount not in excluded}  # fmt: skip


def _noise_amounts(ext: PageExtraction) -> set[Decimal]:
    """Amounts LLM 2 itself listed as delivery or finance charges: never competing product prices."""
    return {m.amount for r in ignored_price_details(ext)
            if r.category in (IgnoredPriceCategory.DELIVERY, IgnoredPriceCategory.FINANCE)
            for m in find_prices(r.text)}  # fmt: skip


def _issue_amounts(issues: list[IssueRecord], excluded: set[Decimal] = frozenset()) -> tuple[set[Decimal], str | None]:
    """Current product amounts named by verified price issues that show more than one amount."""
    for i in issues:
        if i.verified and i.kind in (IssueKind.MULTIPLE_PRICES, IssueKind.CONFLICTING_INFORMATION) \
                and "price" in i.fields:  # fmt: skip
            amounts = set().union(*(_current_amounts(q, excluded) for q in i.quotes))
            if len(amounts) > 1:
                return amounts, i.description
    return set(), None


def _multiple_prices_reason(amount: Decimal | None, option_prices: list[OptionPriceEvidence],
                            issues: list[IssueRecord], excluded: set[Decimal] = frozenset()) -> str | None:  # fmt: skip
    """Why the page's current price cannot be tied to one product/variant, or None."""
    amounts = {o.amount for o in option_prices if o.status is FieldStatus.EXTRACTED}
    if amount is not None:
        amounts.add(amount)
    if len(amounts) > 1:
        shown = ", ".join(str(a) for a in sorted(amounts))
        return (f"several current prices are shown ({shown}) and cannot be reliably associated with the "
                "catalogue product or its variant; no price is selected")  # fmt: skip
    issue_amounts, description = _issue_amounts(issues, excluded)
    if issue_amounts:
        return f"the page shows several possible current prices ({description}); no price is selected"
    return None


def _associate_variant_price(
    ext: PageExtraction, prof: ProductProfile, price_ev: FieldEvidence, amount: Decimal | None,
    currency: str | None, checked: CheckedExtraction, multiple_reason: str,
) -> tuple[FieldEvidence, Decimal | None, str | None]:  # fmt: skip
    """Several current prices: keep one only if exactly one verified option matches the catalogue variant."""
    f = ExtractedField.PRICE

    def ambiguous(note: str) -> tuple[FieldEvidence, None, None]:
        return FieldEvidence(field=f, status=FieldStatus.AMBIGUOUS, value=price_ev.value, quote=price_ev.quote,
                             note=note), None, None  # fmt: skip

    if ext.page_type is not PageType.PRODUCT_PAGE:
        return ambiguous(f"{multiple_reason}; page is not a single-product page")
    verified = [o for o in checked.option_prices if o.status is FieldStatus.EXTRACTED]
    candidates = [OptionCandidate(o.option, o.amount, o.currency, o.price_text, o.quote, "option") for o in verified]
    selected = [o.selected_value for o in checked.options if o.selected_value]
    if amount is not None and selected:  # the main price belongs to the options shown as selected
        candidates.append(OptionCandidate(" ".join(selected), amount, currency, price_ev.value, price_ev.quote,
                                          "selected"))  # fmt: skip
    issue_amounts, _ = _issue_amounts(checked.issues, _noise_amounts(ext))
    if issue_amounts - {c.amount for c in candidates} - ({amount} if amount is not None else set()):
        return ambiguous(f"{multiple_reason}; a verified issue names prices that no option accounts for")
    title = checked.values.get(ExtractedField.PRODUCT_TITLE) or ""
    result = associate(prof, candidates, title)
    for o in verified:
        o.variant_checks = result.checks.get(o.option, {})
        o.matches_catalogue_variant = result.chosen is not None and result.chosen.source == "option" \
            and o.option == result.chosen.label  # fmt: skip
    chosen = result.chosen
    if chosen is None:
        return ambiguous(f"{multiple_reason.split(' and cannot')[0]}: {result.reason}")
    stated = (ext.currency or "").strip().upper() or None
    if not chosen.currency or (stated and stated != chosen.currency):
        return ambiguous(f"{result.reason}, but its currency is missing or contradicts the stated {stated}")
    return (FieldEvidence(field=f, status=FieldStatus.EXTRACTED, value=chosen.price_text, quote=chosen.quote,
                          note=result.reason), chosen.amount, chosen.currency)  # fmt: skip


# Deterministic normalised specification keys. Unlisted names fall back to a slug.
_SPEC_KEYS = {
    "strings": ("strings", "number of strings", "string count", "no. of strings"),
    "finish": ("finish", "colour", "color", "body finish", "body colour", "body color"),
    "handedness": ("handedness", "orientation", "hand", "left/right handed"),
    "body_material": ("body", "body material", "body wood", "body tonewood"),
    "neck_material": ("neck", "neck material", "neck wood"),
    "fingerboard": ("fingerboard", "fretboard", "fingerboard material", "fretboard material"),
    "frets": ("frets", "number of frets", "fret count"),
    "scale_length": ("scale", "scale length"),
    "pickups": ("pickups", "pickup", "pickup configuration", "pickup layout", "pickups (neck/bridge)"),
    # Stage 6.1: "controls" is its own key (never electronics), and a bare "power" / "output" is
    # resolved from its value by `specs.spec_category` (supply voltage is never output wattage).
    "electronics": ("electronics", "preamp", "onboard electronics", "pickup system"),
    "controls": ("controls", "control", "knobs", "switches"),
    "output_power": ("output power", "wattage", "watts", "power output", "rms power", "rms output"),
    "speakers": ("speaker", "speakers", "speaker configuration"),
    "weight": ("weight",),
    "dimensions": ("dimensions", "size"),
    "power_supply": ("power supply", "power requirements", "power requirement", "power adapter", "adapter",
                     "battery", "batteries", "voltage", "current draw"),
    "recording_time": ("recording time", "max recording time", "maximum recording time"),
}
_SPEC_LOOKUP = {alias: key for key, aliases in _SPEC_KEYS.items() for alias in aliases}


# Keys whose meaning depends on the category: LLM 2's proposed name may never assign them.
_CATEGORY_KEYS = {"output_power", "power_supply", "controls", "electronics", "pickups"}


def spec_key(name: str, proposed: str | None = None, value: str | None = None) -> str | None:
    """Display key for a specification. Power/output/controls keys follow `spec_category`, so
    "Power: 9V AC Adapter/Battery" is power_supply (never output wattage)."""
    category = spec_category(name, value) if value is not None else None
    if category in (SpecCategory.POWER_SUPPLY, SpecCategory.OUTPUT_POWER, SpecCategory.CONTROLS):
        return category.value
    k = _SPEC_LOOKUP.get(norm(name).strip(" :"))
    if k:
        return k
    k = _SPEC_LOOKUP.get(norm(proposed).strip(" :"))
    if k and k not in _CATEGORY_KEYS:
        return k
    slug = re.sub(r"[^a-z0-9]+", "_", norm(name)).strip("_")[:40]
    return slug or None


def check_extraction(ext: PageExtraction, page_text: str, page_title: str | None,
                     product: StompboxProduct | None = None) -> CheckedExtraction:  # fmt: skip
    """Apply the deterministic evidence checks to one page's LLM 2 (Task B) output.

    With the catalogue `product`, several current prices are resolved only when exactly one
    verified option matches its variant (`variants.associate`); without it they stay AMBIGUOUS.
    """
    page = norm(f"{page_title or ''}\n{page_text}")
    out = CheckedExtraction()
    checks = [
        _check_text_field(ExtractedField.PRODUCT_TITLE, ext.product_title, ext.title_quote, page),
        _check_text_field(ExtractedField.BRAND, ext.brand, ext.brand_quote, page),
        _check_text_field(ExtractedField.MODEL, ext.model, ext.model_quote, page, flexible=True),
        _check_text_field(ExtractedField.SKU, ext.sku, ext.sku_quote, page, flexible=True),
        _check_text_field(ExtractedField.AVAILABILITY, ext.availability, ext.availability_quote, page),
    ]
    out.option_prices = _check_option_prices(ext, page)
    out.options = _check_options(ext, page)
    out.issues = _check_issues(ext, page)
    for ev in checks:
        out.values[ev.field] = ev.value if ev.status is FieldStatus.EXTRACTED else None
    price_ev, amount, currency = _check_price(ext, page)
    has_option_prices = any(o.status is FieldStatus.EXTRACTED for o in out.option_prices)
    if price_ev.status is FieldStatus.EXTRACTED or (price_ev.status is FieldStatus.NOT_FOUND and has_option_prices):
        main = amount if price_ev.status is FieldStatus.EXTRACTED else None
        why = _multiple_prices_reason(main, out.option_prices, out.issues, _noise_amounts(ext))
        if why is None and main is None:  # only option prices, no main price: they must be associated too
            why = "only per-option prices are shown"
        if why and product is not None:
            price_ev, amount, currency = _associate_variant_price(
                ext, profile(product), price_ev, main, currency, out, why)  # fmt: skip
        elif why and main is not None:
            price_ev = FieldEvidence(field=ExtractedField.PRICE, status=FieldStatus.AMBIGUOUS, value=price_ev.value,
                                     quote=price_ev.quote, note=why)  # fmt: skip
            amount = currency = None
    checks.append(price_ev)
    out.evidence.extend(checks)
    if price_ev.status is FieldStatus.EXTRACTED:
        out.price, out.currency, out.price_text = amount, currency, price_ev.value
    # Specifications: keep only name/value pairs whose value is in the page text.
    for spec in ext.specifications[:MAX_SPECS]:
        if spec.value.strip() and norm(spec.value) in page:
            name = spec.name.strip()[:80]
            value = spec.value.strip()[:MAX_TEXT]
            out.specifications.append(NormalizedSpec(name=name, value=value, key=spec_key(name, spec.normalized_name, value),
                                                     category=spec_category(name, value)))  # fmt: skip
    # Only sale wording counts: delivery, finance and other ignored amounts never set the flag (Stage 6.1).
    out.ignored_price_details = ignored_price_details(ext)
    out.sale_indicated = (any(r.category is IgnoredPriceCategory.SALE for r in out.ignored_price_details)
                          or mentions_sale(ext.price_quote))
    avail = next(e for e in checks if e.field is ExtractedField.AVAILABILITY)
    contradicted = any(i.verified and i.kind is IssueKind.CONFLICTING_INFORMATION and "availability" in i.fields
                       for i in out.issues)  # fmt: skip
    out.availability_status, out.availability_reason = normalize_availability(
        avail.value, verified=avail.status is FieldStatus.EXTRACTED, page_text=page_text,
        proposal=ext.availability_normalized, contradicted=contradicted)  # fmt: skip
    return out


# --------------------------------------------------------------------------- Task A


_COSMETIC = re.compile(
    r"^(?:(?:body|top|guitar|cabinet|product|finish)\s+)?(?:finish|colou?r|colou?rway|colou?r\s*/\s*finish"
    r"|finish\s*/\s*colou?r|colou?r\s+finish|paint(?:work)?)$"
)


def is_cosmetic(attribute: str) -> bool:
    """Only finish / colour differences are cosmetic (approved decision U-1)."""
    return bool(_COSMETIC.match(norm(attribute).strip(" :")))


def check_match_proposal(proposal: MatchProposal, page_text: str, page_title: str | None,
                         request_ids: list[str] | None = None) -> tuple[ProposalStatus, MatchProposalRecord]:  # fmt: skip
    """Verify a Task A proposal's quotes against the page (never the catalogue product)."""
    page = norm(f"{page_title or ''}\n{page_text}")
    reasons = []
    for r in proposal.reasons[:MAX_REASONS]:
        quote = (r.quote or "").strip()[:MAX_TEXT * 2] or None
        reasons.append(VerifiedReason(text=r.text.strip()[:MAX_TEXT], quote=quote,
                                      verified=_quote_in_page(quote, page)))  # fmt: skip
    diffs = []
    for d in proposal.differing_attributes[:MAX_REASONS]:
        quote = (d.quote or "").strip()[:MAX_TEXT * 2] or None
        value = (d.page_value or "").strip()[:MAX_TEXT] or None
        ok = _quote_in_page(quote, page) and value is not None and norm(value) in norm(quote)
        diffs.append(VerifiedDifference(attribute=d.attribute.strip()[:60], catalogue_value=d.catalogue_value,
                                        page_value=value, quote=quote, verified=ok,
                                        cosmetic=is_cosmetic(d.attribute)))  # fmt: skip
    record = MatchProposalRecord(
        classification=proposal.classification, corresponding_status=PROPOSAL_STATUS[proposal.classification],
        reasons=reasons, differing_attributes=diffs, confidence=proposal.confidence,
        notes=(proposal.notes or "").strip()[:500] or None, request_ids=list(request_ids or []),
    )  # fmt: skip
    verified = any(r.verified for r in reasons) or any(d.verified for d in diffs)
    return (ProposalStatus.COMPLETED if verified else ProposalStatus.UNSUPPORTED), record


# --------------------------------------------------------------------------- search texts


_SKU_LIKE = re.compile(r"[A-Za-z0-9]")


def product_queries(brand: str, model: str, name: str, specifications: str | None, n: int) -> list[str]:
    """Search texts for one product: a combined brand+model+name query, then (if n=2)
    an exact "brand model" query. Variant details (handedness, finish) live in the name."""
    combined = name if mentions_model(model, name) and mentions_model(brand, name) else f"{brand} {model} {name}"
    if specifications:
        first = re.split(r"[;\n|]", specifications)[0].strip()
        if first and _SKU_LIKE.search(first) and len(first) <= 60:
            combined = f"{combined} {first}"
    queries = [" ".join(combined.split())[:300]]
    exact = f"{brand} {model}"
    if n >= 2 and norm(exact) != norm(queries[0]):
        queries.append(exact)
    return queries
