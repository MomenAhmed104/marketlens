"""Deterministic scoring helpers for the search-provider benchmark.

These are measurement heuristics for comparing providers, not the production
matching rules (Phase 6) or price extraction (Phase 5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
import re
from decimal import Decimal
from urllib.parse import unquote, urlsplit

_SEP = r"[\s\-_./'’‘´`]*"
# £1,234.56 / £ 99 / GBP 99.00. Excludes values followed by "k" or "m" (e.g. "£2k").
_GBP = re.compile(
    r"(?:£\s?|\bGBP\s?)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?(?![\d.,]*\s?[kKmM]\b)"
)


def model_tokens(model: str) -> list[str]:
    return [t for t in re.split(r"[^0-9a-z]+", model.casefold()) if t]


def model_pattern(model: str) -> re.Pattern[str]:
    """Regex matching the model with flexible separators and word boundaries.

    Separators are optional between every character, so "RC-5" matches "RC-5",
    "rc5", "rc 5", "boss-rc-5-loop" and "MKII" matches "MK II", but word
    boundaries still stop "RC-5" matching "RC-500".
    """
    chars = "".join(model_tokens(model))
    if not chars:
        raise ValueError(f"Model {model!r} has no alphanumeric tokens")
    body = _SEP.join(re.escape(c) for c in chars)
    return re.compile(rf"(?<![0-9a-z]){body}(?![0-9a-z])", re.IGNORECASE)


def mentions_model(model: str, *texts: str | None) -> bool:
    pattern = model_pattern(model)
    return any(pattern.search(t) for t in texts if t)


def url_text(url: str) -> str:
    """Path of a URL as searchable text (slugs often contain the model)."""
    return unquote(urlsplit(url).path)


def gbp_prices(text: str | None) -> list[Decimal]:
    """All GBP amounts in text, in order of appearance (duplicates kept)."""
    if not text:
        return []
    out = []
    for whole, pence in _GBP.findall(text):
        out.append(Decimal(whole.replace(",", "") + "." + (pence or "00")))
    return out


def is_uk_host(hostname: str) -> bool:
    return hostname.endswith(".uk")


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile; None for no data."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, math.ceil(pct / 100 * len(ordered)) - 1)
    return ordered[k]


# --------------------------------------------------------------------------- identity

class Identity:
    EXACT = "EXACT"  # base model + all required variants, nothing forbidden
    VARIANT_MISMATCH = "VARIANT_MISMATCH"  # base model, but wrong/missing variant
    CONDITION_MISMATCH = "CONDITION_MISMATCH"  # pre-owned, B-stock, ex-demo...
    ACCESSORY = "ACCESSORY"  # a different product made for this one (PSU, case...)
    REVIEW = "REVIEW"  # cannot decide automatically
    NO_MATCH = "NO_MATCH"  # base model absent


_CONDITION = re.compile(
    r"pre[\s-]?owned|second[\s-]?hand|\bused\b|b[\s-]?stock|ex[\s-]?demo|ex[\s-]?display"
    r"|refurbished|open[\s-]?box|\bnearly[\s-]?new\b|segunda[\s-]?mano",
    re.IGNORECASE,
)
# Accessory nouns (not when bundled: "... with case") or "<accessory> for <model>".
_ACCESSORY = re.compile(
    r"(?<!with )\b(power[\s-]?supply|psu|adapter|hard[\s-]?case|flight[\s-]?case|flightcase|gig[\s-]?bag"
    r"|dust[\s-]?cover|cover|cables?|footswitch|replacement|spare)\b",
    re.IGNORECASE,
)
_FOR_MODEL = re.compile(r"\bfor\b", re.IGNORECASE)


def is_accessory(spec, text: str) -> bool:
    if _ACCESSORY.search(text):
        return True
    m = model_pattern(spec.base_model).search(text)
    # "... for <model>": the word "for" shortly before the model name.
    return bool(m and _FOR_MODEL.search(text[max(0, m.start() - 40): m.start()]))
_TRUNCATED = re.compile(r"(\.\.\.|…)\s*$")


def identity_text(title: str | None, url: str) -> str:
    """Title plus URL path, with slug separators turned into spaces."""
    slug = re.sub(r"[_\-/.+]+", " ", url_text(url))
    return f"{title or ''} {slug}"


def brand_present(spec, brand: str | None, text: str) -> bool:
    if spec.brand_pattern:
        return re.search(spec.brand_pattern, text, re.IGNORECASE) is not None
    return brand is None or mentions_model(brand, text)


def classify_identity(spec, title: str | None, url: str, brand: str | None = None) -> str:
    """Deterministic identity class of a page from its title and URL.

    EXACT needs the base model, every required variant, the brand (when given)
    and every `review_if_missing` term, with no forbidden, condition or review term.
    """
    text = identity_text(title, url)
    if not mentions_model(spec.base_model, text):
        return Identity.NO_MATCH
    if any(re.search(f, text, re.IGNORECASE) for f in spec.forbidden):
        return Identity.VARIANT_MISMATCH
    if is_accessory(spec, text):
        return Identity.ACCESSORY
    if _CONDITION.search(text):
        return Identity.CONDITION_MISMATCH
    missing =[g for g in spec.required_variants if not re.search(g, text, re.IGNORECASE)]
    if missing:
        # A truncated title may simply have cut the variant off.
        return Identity.REVIEW if title and _TRUNCATED.search(title) else Identity.VARIANT_MISMATCH
    if not brand_present(spec, brand, text):
        return Identity.REVIEW  # e.g. a Gibson page for an Epiphone product
    if any(re.search(t, text, re.IGNORECASE) for t in spec.review_terms):
        return Identity.REVIEW
    if any(not re.search(t, text, re.IGNORECASE) for t in spec.review_if_missing):
        return Identity.REVIEW
    return Identity.EXACT


# --------------------------------------------------------------------------- page type

# Gear4music product IDs seen: 3LJT, 40BW, 70E5, 3GMC, 399X (always contain a digit).
_G4M_ID = re.compile(r"^(?=[A-Za-z]*\d)[A-Za-z0-9]{3,6}$")


def page_type(hostname: str, canonical: str) -> str:
    """'product', 'category', 'content' or 'unknown', from known URL patterns."""
    path = urlsplit(canonical).path
    segs = [s for s in path.split("/") if s]
    low = path.lower()
    if hostname.startswith("blog.") or any(k in low for k in ("/blog/", "/news/", "/learn/")) or low.endswith("-guide"):
        return "content"
    if hostname.endswith("guitarguitar.co.uk"):
        return "product" if low.startswith("/product/") else "category"
    if hostname.endswith("thomann.co.uk"):
        return "product" if low.endswith(".htm") else "category"
    if hostname.endswith("gear4music.com"):
        return "product" if len(segs) >= 3 and _G4M_ID.match(segs[-1]) else "category"
    if hostname.endswith("andertons.co.uk"):
        return "category" if "/browse/" in low or len(segs) != 1 else "product"
    return "unknown"


# --------------------------------------------------------------------------- price attribution

_PRICE = re.compile(r"(?:£\s?|\bGBP\s?)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?")
# Words that mark an amount as something other than this product's price.
_NOT_PRODUCT_PRICE_BEFORE = re.compile(
    r"\b(over|orders?|spend(?:ing)?|save|saving|rrp|was|recommended(?: retail)? price|list price|every|"
    r"finance|per month|deposit|from|"
    r"basket|delivery|shipping|voucher|credit|threshold|worth|up to|get)\W*$",
    re.IGNORECASE,
)
_NOT_PRODUCT_PRICE_AFTER = re.compile(
    r"^\s*(off|per month|/\s?month|a month|p/m|delivery|to spend|you spend)", re.IGNORECASE
)
_AVAILABILITY = re.compile(
    r"not (?:currently )?in stock|no longer available|"
    r"in stock within [^.\n|]{1,25}|in stock|out of stock|currently not available|"
    r"not available|currently unavailable|pre[\s-]?order|sold out|discontinued|"
    r"coming soon|available to order|back[\s-]?order",
    re.IGNORECASE,
)


_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def strip_markdown_links(text: str) -> str:
    """Drop images and keep only link text, so long URLs don't distort distances."""
    return _MD_LINK.sub(r"\1", _MD_IMAGE.sub("", text))


@dataclass
class PriceEvidence:
    price: Decimal | None
    context: str | None
    candidates: int  # distinct plausible product prices after the heading
    availability: str | None


def attribute_price(content: str | None, base_model: str, window: int = 2500) -> PriceEvidence:
    """Pick the first plausible product price after the first mention of the model.

    Skips amounts that read as delivery thresholds, savings, RRP/was prices,
    finance, basket totals or zero. A benchmark heuristic, not Phase 5 extraction.
    """
    if not content:
        return PriceEvidence(None, None, 0, None)
    content = strip_markdown_links(content)
    m = model_pattern(base_model).search(content)
    if not m:
        return PriceEvidence(None, None, 0, None)
    region = content[m.start(): m.start() + window]
    chosen, ctx, seen = None, None, set()
    for pm in _PRICE.finditer(region):
        before = region[max(0, pm.start() - 40): pm.start()]
        after = region[pm.end(): pm.end() + 15]
        value = Decimal(pm.group(1).replace(",", "") + "." + (pm.group(2) or "00"))
        if value == 0 or _NOT_PRODUCT_PRICE_BEFORE.search(before) or _NOT_PRODUCT_PRICE_AFTER.search(after):
            continue
        seen.add(value)
        if chosen is None:
            chosen = value
            ctx = region[max(0, pm.start() - 80): pm.end() + 60].replace("\n", " ")
    avail = _AVAILABILITY.search(region)
    return PriceEvidence(chosen, ctx, len(seen), avail.group(0).strip() if avail else None)
