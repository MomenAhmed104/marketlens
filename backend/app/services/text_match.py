"""Deterministic text-matching helpers shared by discovery and the benchmark.

These detect mentions; they do not decide product identity (Phase 6).
"""

from __future__ import annotations

import re
from decimal import Decimal
from functools import lru_cache
from urllib.parse import unquote, urlsplit

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
