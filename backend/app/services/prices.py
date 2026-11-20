"""Deterministic price-text parsing for extraction checks (Phase 5).

Only parsing and context detection live here: it turns text such as
"Was £599.00 Now £579.00" into amounts with their currency and position, and
says whether an amount is presented as a crossed-out/original/saving figure
or as a conditional price ("from £x", "£x per month").

No price comparison or validation against the catalogue happens here: that is
Phase 6.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Symbol -> ISO code. "$" alone is ambiguous (USD, AUD, CAD...), so it gives no currency.
_SYMBOLS = {"£": "GBP", "€": "EUR", "$": None, "US$": "USD"}
_CODES = {"GBP", "EUR", "USD"}

_AMOUNT = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
_PRICE = re.compile(
    rf"(?P<pre>US\$|£|€|\$|\b(?:GBP|EUR|USD)\b)\s?(?P<amt>{_AMOUNT})(?![\d%]|\s?[kKmM]\b)"
    rf"|(?<![\w.,])(?P<amt2>{_AMOUNT})\s?(?P<post>\b(?:GBP|EUR|USD)\b)",
    re.IGNORECASE,
)

# Words that mark an amount as NOT the current selling price when they come
# just before it ("Was £599", "RRP £649", "Save £20", "Original price £599").
# "Regular price" is deliberately absent: Shopify uses it for the normal price.
_SALE_BEFORE = re.compile(
    r"(?:\bwas|\brrp|\br\.r\.p\.?|\bsave|\bsaving|\byou save|\boriginally|\boriginal price|\blist price"
    r"|\bprevious price|\breduced from|\bcompare at|\bmsrp|\bdiscount|\bstrike)\W{0,4}(?:\bprice\W{0,3})?$",
    re.IGNORECASE,
)
# "Regular/normal price £219 Special/Sale/Offer price £197" (Shopify, Magento...):
# the regular amount is the original only when a current-price label is also shown.
_REGULAR_BEFORE = re.compile(r"\b(?:regular|normal|usual)\s+price\W{0,4}$", re.IGNORECASE)
_SALE_PRICE_LABEL = re.compile(
    r"\b(?:sale|special|offer|deal|promo(?:tional)?|reduced|now)\s+price\b|\bnow\s*(?=£|€|\$|\d)", re.IGNORECASE
)
_SALE_AFTER = re.compile(r"^\W{0,3}(?:off\b|saving\b|discount\b|less\b)", re.IGNORECASE)

# Conditional amounts: not a single standard displayed price.
_CONDITIONAL_BEFORE = re.compile(
    r"(?:\bfrom|\bstarting at|\bas low as|\bdeposit(?: of)?|\bfinance(?: from)?|\bmonthly|\bpay)\W{0,4}$", re.IGNORECASE
)
_CONDITIONAL_AFTER = re.compile(
    r"^\s*(?:/\s*(?:mo|month|m)\b|per month\b|a month\b|p/?m\b|pcm\b|monthly\b|deposit\b|x\s?\d+\b|for \d+ months\b)",
    re.IGNORECASE,
)
_WINDOW = 30


@dataclass(frozen=True)
class PriceMention:
    amount: Decimal
    currency: str | None  # ISO code, or None when no unambiguous currency is shown
    start: int
    end: int
    text: str  # the matched text, e.g. "£1,299.00"


def find_prices(text: str | None) -> list[PriceMention]:
    """Every money amount with an explicit currency marker, in order."""
    out: list[PriceMention] = []
    for m in _PRICE.finditer(text or ""):
        raw = m.group("amt") or m.group("amt2")
        marker = (m.group("pre") or m.group("post") or "").upper()
        currency = marker if marker in _CODES else _SYMBOLS.get(marker)
        try:
            amount = Decimal(raw.replace(",", ""))
        except InvalidOperation:  # pragma: no cover - regex guarantees digits
            continue
        out.append(PriceMention(amount, currency, m.start(), m.end(), m.group(0)))
    return out


def parse_single_price(text: str | None) -> PriceMention | None:
    """The amount in a short price string such as "£579.00". None unless exactly one."""
    found = find_prices(text)
    return found[0] if len(found) == 1 else None


def is_sale_context(text: str, mention: PriceMention) -> bool:
    """True if the amount is shown as a was/RRP/original/saving figure."""
    before = text[max(0, mention.start - _WINDOW): mention.start]
    after = text[mention.end: mention.end + _WINDOW]
    if _SALE_BEFORE.search(before) or _SALE_AFTER.search(after):
        return True
    return bool(_REGULAR_BEFORE.search(before) and _SALE_PRICE_LABEL.search(text))


def is_conditional_context(text: str, mention: PriceMention) -> bool:
    """True for "from £x", "£x per month", "deposit £x" and similar."""
    before = text[max(0, mention.start - _WINDOW): mention.start]
    after = text[mention.end: mention.end + _WINDOW]
    return bool(_CONDITIONAL_BEFORE.search(before) or _CONDITIONAL_AFTER.search(after))


# --------------------------------------------------------------------------- delivery charges (Stage 6.1)

# Delivery / shipping wording. A delivery charge is never a product price and never sale evidence.
_DELIVERY_WORDS = re.compile(
    r"\b(?:delivery|deliveries|shipping|postage|p\s?&\s?p|courier|carriage|click\s*(?:&|and)\s*collect"
    r"|collect(?:ion)?\s+in[\s-]store|dispatch)\b",
    re.IGNORECASE,
)
# Delivery wording just before an amount ("Next day delivery: £4.99", "SATURDAY delivery, 1hr window £7.49").
_DELIVERY_BEFORE = re.compile(
    r"\b(?:delivery|shipping|postage|p\s?&\s?p|courier|carriage|click\s*(?:&|and)\s*collect)\b(?P<gap>[^£€$\d]{0,30})$",
    re.IGNORECASE,
)
# ... or just after it ("£4.99 delivery"), but not "£219 delivery included".
_DELIVERY_AFTER = re.compile(r"^\s*\(?\s*(?:delivery|shipping|postage|p\s?&\s?p)\b(?!\s+(?:included|inc\b|free))",
                             re.IGNORECASE)  # fmt: skip
_FREE_BEFORE = re.compile(r"\bfree\s+(?:\w+\s+){0,2}$", re.IGNORECASE)  # "Free (UK) delivery £219"
_PRICE_LABEL = re.compile(r"\b(?:price|now|our|only|just|for)\b", re.IGNORECASE)  # "delivery available. Our price £219"
_SALE_WORDS = re.compile(
    r"\b(?:was|rrp|r\.r\.p|save|saving|you\s+save|originally|original\s+price|list\s+price|compare\s+at|msrp"
    r"|reduced|crossed[\s-]out|sale|clearance)\b|\d+\s?%\s?off\b",
    re.IGNORECASE,
)
_FINANCE_WORDS = re.compile(r"\b(?:finance|per\s+month|monthly|deposit|instal?ments?|apr|klarna|paypal\s+credit)\b",
                            re.IGNORECASE)  # fmt: skip


def is_delivery_context(text: str, mention: PriceMention) -> bool:
    """True when the amount is a delivery/shipping charge ("Next day delivery £4.99", "£3.49 postage")."""
    before = text[max(0, mention.start - 40): mention.start]
    after = text[mention.end: mention.end + 25]
    m = _DELIVERY_BEFORE.search(before)
    if m and not _FREE_BEFORE.search(before[: m.start()]) and not _PRICE_LABEL.search(m.group("gap")):
        return True
    return bool(_DELIVERY_AFTER.match(after))


# A discount that applies to delivery, not to the product ("Save £5 on delivery", "£5 off shipping",
# "Delivery: save £5", "50% off delivery"). Stage 6.1.1.
_DELIVERY_DISCOUNT_AFTER = re.compile(
    r"^\W{0,3}(?:off\s+)?(?:on|off|for|towards)?\s*(?:your\s+|the\s+|all\s+|uk\s+|standard\s+|next[\s-]day\s+)*"
    r"(?:delivery|shipping|postage|p\s?&\s?p|carriage|click\s*(?:&|and)\s*collect)\b",
    re.IGNORECASE,
)
# "Delivery: save £5" / "Shipping discount £5". Only a saving word straight after the delivery word, so
# "Free delivery. Was £219 Now £189" stays a product sale.
_DELIVERY_DISCOUNT_BEFORE = re.compile(
    r"\b(?:delivery|shipping|postage|p\s?&\s?p|carriage)\b\W{0,3}(?:save|saving|discount|reduced\s+by)\W{0,3}$",
    re.IGNORECASE,
)
_PERCENT_OFF = re.compile(r"\d+\s?%\s?off\b", re.IGNORECASE)
_SALE_FLAG_WORDS = re.compile(r"\bsale\b|\bclearance\b", re.IGNORECASE)


def is_delivery_discount(text: str, mention: PriceMention) -> bool:
    """True when a saving amount applies to delivery rather than the product."""
    after = text[mention.end: mention.end + 40]
    before = text[max(0, mention.start - 30): mention.start]
    return bool(_DELIVERY_DISCOUNT_AFTER.match(after) or _DELIVERY_DISCOUNT_BEFORE.search(before))


def is_product_sale(text: str, mention: PriceMention) -> bool:
    """A was/RRP/original/saving amount that is about the product, not about delivery."""
    return (is_sale_context(text, mention) and not is_delivery_discount(text, mention)
            and not is_delivery_context(text, mention))  # fmt: skip


def ignored_price_category(text: str | None) -> str:
    """Why LLM 2 ignored a price text: "sale", "delivery", "finance" or "other".

    A product sale amount (was / RRP / original / saving / crossed-out) is "sale" even when the
    text also mentions delivery ("Was £10 and free delivery"). A delivery charge or a delivery
    discount ("Save £5 on delivery") is "delivery" and never sale evidence.
    """
    t = text or ""
    mentions = find_prices(t)
    if any(is_product_sale(t, m) for m in mentions):
        return "sale"
    if _DELIVERY_WORDS.search(t):
        return "delivery"
    if _SALE_WORDS.search(t):
        return "sale"
    if any(is_conditional_context(t, m) for m in mentions) or _FINANCE_WORDS.search(t):
        return "finance"
    return "other"


def mentions_sale(text: str | None) -> bool:
    """Whether the text presents a product sale: a was/RRP/original/saving amount about the product,
    "N% off" not about delivery, or "sale"/"clearance". Delivery discounts never count."""
    text = text or ""
    if any(is_product_sale(text, m) for m in find_prices(text)):
        return True
    if any(not _DELIVERY_DISCOUNT_AFTER.match(text[m.end(): m.end() + 40]) for m in _PERCENT_OFF.finditer(text)):
        return True
    return bool(_SALE_FLAG_WORDS.search(text))
