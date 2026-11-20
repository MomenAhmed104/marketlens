"""Deterministic availability normalisation (approved decisions P-5, U-3; Stage 6.3.1).

Maps the *verified* availability text of a retrieved page to one of six
statuses. LLM 2 may propose a status; it never decides one:

- No verified text (missing, unsupported quote, no LLM) -> UNKNOWN.
- A geographic or delivery restriction in the availability text ("currently
  unavailable for delivery to your country", "cannot be shipped to your region")
  -> UNKNOWN, whatever else the text or LLM 2 says. The page was rendered for a
  visitor the retailer does not deliver to, so it does not show the product's
  stock status (Stage 6.3.1; seen on Gear4music in Stage 6.2).
- Generic unavailability wording ("currently unavailable", "not available",
  "not available for purchase") -> OUT_OF_STOCK only when the page shows no
  geographic or delivery restriction anywhere; with one, it could be caused by
  the restriction, so UNKNOWN.
- Wording from more than one status, or none recognised -> UNKNOWN.
- LLM 2's proposal disagreeing with the mapping -> UNKNOWN, with one exception:
  explicit stock wording about the product ("Out of stock", "Sold out", "Not in
  stock", "No stock") stays OUT_OF_STOCK when LLM 2 proposes only UNKNOWN (an
  unsure proposal never removes a clear, verified statement; Stage 6.3.1).
- A verified LLM 2 issue saying the availability is contradictory -> UNKNOWN.
- Explicit low-stock wording ("Only 2 left", "Low stock") -> IN_STOCK only when
  the page also shows the product can be bought now (in stock, add to basket,
  buy now); otherwise UNKNOWN.

Search not finding a page never makes a product unavailable (CLAUDE.md §10).
"""

from __future__ import annotations

import re

from app.schemas.common import AvailabilityStatus as A

_F = re.IGNORECASE

# Explicit statements that the product itself has no stock: OUT_OF_STOCK even when LLM 2 is unsure.
_EXPLICIT_OUT_OF_STOCK = re.compile(r"\bout[- ]of[- ]stock\b|\bsold[- ]out\b|\bnot (?:currently )?in stock\b"
                                    r"|\bno stock\b", _F)  # fmt: skip
# Generic unavailability: the product cannot be bought, but the wording does not say why.
_GENERIC_UNAVAILABLE = re.compile(r"\b(?:currently |temporarily )?unavailable\b|\bnot (?:currently )?available\b", _F)

# Geographic or delivery restrictions (Stage 6.3.1). Strict: a restriction verb or phrase,
# followed in the same clause by a destination ("your country", "this region", "the UK"),
# or "not available for delivery/shipping". Footer wording such as "we deliver to over 100
# countries" has no restriction phrase and never matches.
_RESTRICTED = (r"(?:unavailable|not (?:currently )?available|not (?:be )?(?:delivered|shipped|sold|offered)"
               r"|(?:cannot|can't|can not|could not|couldn't) (?:be )?(?:delivered|shipped|deliver|ship|sold)"
               r"|(?:do|does|will|we) not (?:deliver|ship|sell)|(?:don't|doesn't|won't) (?:deliver|ship|sell)"
               r"|not (?:deliverable|shippable))")  # fmt: skip
_DESTINATION = (r"(?:(?:your|this|that|the selected|selected|your selected|your current|the chosen|the)\s+"
                r"(?:shipping\s+|delivery\s+)?(?:country|countries|region|location|area|territory|address|postcode"
                r"|post code|zip code|destination)"
                r"|(?:the\s+)?(?:UK|U\.K\.|United Kingdom|Great Britain|Britain|England|Scotland|Wales"
                r"|Northern Ireland))\b")  # fmt: skip
_GEO_RESTRICTION = re.compile(
    rf"\b{_RESTRICTED}\b[^.;!?\n]{{0,40}}?\b(?:to|in|into|for|within)\s+{_DESTINATION}"
    rf"|\bno (?:delivery|deliveries|shipping)\s+(?:to|in|into|within)\s+{_DESTINATION}"
    r"|\b(?:unavailable|not (?:currently )?available)\s+for\s+(?:delivery|shipping|dispatch)\b", _F)

# Negative / non-current patterns first: their spans are masked before "in stock" is looked for,
# so "Not in stock" or "In stock soon" never count as in stock.
_PATTERNS: list[tuple[A, re.Pattern[str]]] = [
    (A.DISCONTINUED, re.compile(r"\bdiscontinued\b|\bno longer (?:available|made|produced|stocked|sold|manufactured)\b"
                                r"|\bend of line\b|\bend-of-line\b", _F)),
    (A.OUT_OF_STOCK, re.compile(_EXPLICIT_OUT_OF_STOCK.pattern + "|" + _GENERIC_UNAVAILABLE.pattern, _F)),
    (A.PREORDER, re.compile(r"\bpre[- ]?order(?:s|ed|ing)?\b", _F)),
    (A.BACKORDER, re.compile(r"\bback[- ]?order(?:s|ed)?\b|\bon order\b|\bawaiting (?:new )?stock\b"
                             r"|\bavailable to order\b|\bspecial order\b|\bin stock (?:soon|from|on)\b"
                             r"|\bexpected (?:in stock|in|on|date)\b|\bdue in\b", _F)),
    (A.IN_STOCK, re.compile(r"\bin[- ]stock\b|\bavailable now\b|\bavailable for (?:immediate )?dispatch\b"
                            r"|\bready to (?:ship|dispatch)\b|\bin store and online\b", _F)),
]  # fmt: skip
_LOW_STOCK = re.compile(r"\bonly \d+ (?:left|remaining)\b|\blow (?:in )?stock\b|\blimited stock\b"
                        r"|\b(?:last|final) (?:one|few|\d+)(?: left| remaining| in stock)?\b|\bfew left\b", _F)  # fmt: skip
_BUYABLE = re.compile(r"\badd to (?:basket|cart|bag|trolley)\b|\bbuy (?:it )?now\b|\bin[- ]stock\b", _F)


def geographic_restriction(text: str | None) -> str | None:
    """The first geographic or delivery-restriction phrase in the text, or None."""
    m = _GEO_RESTRICTION.search(text or "")
    return m.group(0) if m else None


def classify_text(text: str | None) -> tuple[set[A], bool]:
    """(statuses whose wording appears in the text, low-stock wording present)."""
    t = text or ""
    found: set[A] = set()
    masked = t
    for status, pattern in _PATTERNS:
        for m in pattern.finditer(masked):
            found.add(status)
            masked = masked[: m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
    return found, bool(_LOW_STOCK.search(t))


def normalize_availability(
    text: str | None,
    *,
    verified: bool,
    page_text: str = "",
    proposal: A | None = None,
    contradicted: bool = False,
) -> tuple[A, str]:
    """Return (status, reason). `verified`: the availability quote and value passed the evidence checks."""
    if not text or not verified:
        return A.UNKNOWN, "No verified availability text on the page"
    restriction = geographic_restriction(text)
    if restriction:
        return A.UNKNOWN, (f"Availability text {text!r} is a geographic or delivery restriction ({restriction!r}), "
                           "not a stock status")  # fmt: skip
    if contradicted:
        return A.UNKNOWN, "The page gives contradictory availability information"
    found, low = classify_text(text)
    if low and not found - {A.IN_STOCK}:
        if A.IN_STOCK in found or _BUYABLE.search(page_text or ""):
            found = {A.IN_STOCK}
        else:
            return A.UNKNOWN, f"Low-stock wording {text!r} without a sign that the product can be bought now"
    if not found:
        return A.UNKNOWN, f"Availability text {text!r} is not a recognised stock status"
    if len(found) > 1:
        names = ", ".join(sorted(s.value for s in found))
        return A.UNKNOWN, f"Availability text {text!r} mixes several statuses ({names})"
    (status,) = found
    explicit = status is A.OUT_OF_STOCK and bool(_EXPLICIT_OUT_OF_STOCK.search(text))
    if status is A.OUT_OF_STOCK and not explicit:
        page_restriction = geographic_restriction(page_text)
        if page_restriction:
            return A.UNKNOWN, (f"Generic unavailability {text!r} on a page that also shows a geographic or "
                               f"delivery restriction ({page_restriction!r}); left unknown")  # fmt: skip
    if proposal is not None and proposal is not status:
        if explicit and proposal is A.UNKNOWN:
            return status, f"Verified text {text!r} (explicit stock wording; LLM 2's UNKNOWN does not override it)"
        return A.UNKNOWN, (f"LLM 2 proposed {proposal.value} but the verified text {text!r} reads as {status.value}; "
                           "left unknown")  # fmt: skip
    return status, f"Verified text {text!r}"
