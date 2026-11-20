"""Deterministic text-matching helpers shared by discovery and the benchmark.

These detect mentions; they do not decide product identity (Phase 6). `quote_in_evidence`
checks that an LLM-cited quote really is in the evidence it cites.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal
from functools import lru_cache
from typing import Protocol
from urllib.parse import unquote, urlsplit

from app.services.urls import canonical_url

_SEP = r"[\s\-_./'’‘´`]*"
# £1,234.56 / £ 99 / GBP 99.00. Excludes values followed by "k" or "m" (e.g. "£2k").
_GBP = re.compile(r"(?:£\s?|\bGBP\s?)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?(?![\d.,]*\s?[kKmM]\b)")


def model_tokens(model: str) -> list[str]:
    return [t for t in re.split(r"[^0-9a-z]+", model.casefold()) if t]


@lru_cache(maxsize=1024)
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
    return [Decimal(w.replace(",", "") + "." + (p or "00")) for w, p in _GBP.findall(text)]


def is_uk_host(hostname: str) -> bool:
    return hostname.endswith(".uk")


# --------------------------------------------------------------------------- verbatim quotes

# Shorter quotes ("UK", "£99") prove nothing on their own and match too easily.
MIN_QUOTE_CHARS = 10


class QuotableEvidence(Protocol):
    url: str
    canonical_url: str
    title: str | None
    snippet: str | None


def normalise_quote(text: str | None) -> str:
    """Whitespace collapsed and case folded: the only differences a 'verbatim' quote may have."""
    return " ".join((text or "").split()).casefold()


def quote_in_evidence(quote: str | None, url: str | None, evidence: Iterable[QuotableEvidence]) -> bool:
    """True only if `quote` appears verbatim (whitespace and case aside) in the stored title
    or the stored snippet of an evidence item with this URL (as given, or canonical).

    The quote must sit inside one field: text stitched across title and snippet, or
    across two items, does not count. A quote from another URL does not count either,
    even if the same words appear there. Quotes under MIN_QUOTE_CHARS never count."""
    q = normalise_quote(quote)
    if len(q) < MIN_QUOTE_CHARS or not url:
        return False
    wanted = {url.strip()}
    try:
        wanted.add(canonical_url(url))
    except ValueError:
        pass
    for e in evidence:
        if {e.url, e.canonical_url} & wanted and (q in normalise_quote(e.title) or q in normalise_quote(e.snippet)):
            return True
    return False
