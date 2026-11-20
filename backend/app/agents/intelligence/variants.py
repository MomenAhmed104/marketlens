"""Deterministic association of per-option prices with the catalogue variant (audit fix, P-6).

When a page shows several current prices for different options (finishes,
handedness, sizes...), a price is kept only when exactly one verified option
matches every known, relevant variant attribute of the Stompbox product.
Otherwise the price stays AMBIGUOUS. The lowest, first or default-option price
is never picked automatically.

For each option label and attribute the result is:
- MATCH: the label states the catalogue value (or a right-handed / full-size /
  standard default the catalogue implies, written explicitly);
- CONFLICT: the label states a different value;
- UNKNOWN: the attribute matters but cannot be verified (the catalogue value is
  known but the label is silent where it must be stated, or the label varies an
  attribute the catalogue does not state);
- ABSENT: neither side states it and no default is at stake.

An option matches only with no CONFLICT and no UNKNOWN. A finish match alone is
therefore never enough when, for example, handedness could differ: a
left-handed product needs "left-handed" stated, and a "Black Left-Handed"
option conflicts with a right-handed product.

Attributes stated page-wide (in the verified product title) apply to every
option, unless some option label states that attribute itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.services.matching import attributes as A
from app.services.matching.attributes import ProductProfile

MATCH, CONFLICT, UNKNOWN, ABSENT = "MATCH", "CONFLICT", "UNKNOWN", "ABSENT"


def _set_check(stated: set[str], known: set[str], default: set[str]) -> str:
    """Compare a stated set with the catalogue's explicit values (or its implied default)."""
    if stated:
        expected = known or default
        if not expected:
            return UNKNOWN
        return MATCH if stated & expected else CONFLICT
    return UNKNOWN if known else ABSENT


def _finish(prof: ProductProfile, text: str) -> str:
    colours = A.colour_words(text) - prof.name_words
    if prof.finish:
        if A.finish_in(prof.finish, text):  # catalogue finish or a controlled alias
            # A label naming another finish too ("Ice Tea / Ebony") cannot authorise a variant price.
            return UNKNOWN if A.other_finish_colours(prof.finish, text, prof.name_words) else MATCH
        if colours and not colours & prof.finish_colours:
            return CONFLICT
        return UNKNOWN  # silent, or a partial overlap such as "Bourbon Burst" vs "Iced Tea Burst"
    return UNKNOWN if colours else ABSENT  # options vary by colour, the catalogue names none


def _handedness(prof: ProductProfile, text: str) -> str:
    h = A.handedness(text)
    if prof.handedness == "left":
        return MATCH if h == "left" else CONFLICT if h == "right" else UNKNOWN
    return CONFLICT if h == "left" else MATCH if h == "right" else ABSENT  # right-handed is the default


def _configuration(prof: ProductProfile, text: str) -> str:
    cabs, forms = A.cab_configs(text), A.amp_forms(text)
    if cabs:
        return MATCH if cabs == prof.cab_configs else CONFLICT
    if forms and prof.amp_forms and not forms <= prof.amp_forms:
        return CONFLICT
    if prof.cab_configs:  # e.g. "Head only" for a head & 2x12 product: the cabinet is not stated
        return UNKNOWN
    if forms:
        return MATCH if prof.amp_forms else UNKNOWN
    return ABSENT


def _bundle(prof: ProductProfile, text: str) -> str:
    b = A.is_bundle(text)
    if prof.bundle:
        return MATCH if b else UNKNOWN
    return CONFLICT if b else ABSENT


def _electro(prof: ProductProfile, text: str) -> str:
    states = A.electro_states(text)
    if not states:
        return ABSENT
    if prof.electro is None or len(states) > 1:
        return UNKNOWN
    return MATCH if ("electro" in states) == prof.electro else CONFLICT


CHECKS: dict[str, Callable[[ProductProfile, str], str]] = {
    "finish": _finish,
    "handedness": _handedness,
    "size": lambda p, t: _set_check(A.sizes(t), p.sizes, {"full"}),
    "strings": lambda p, t: _set_check(A.string_counts(t), p.strings,
                                       {p.strings_default} if p.strings_default else set()),
    "configuration": _configuration,
    "bundle": _bundle,
    "pickups": lambda p, t: _set_check(A.pickup_configs(t), p.pickups, set()),
    "electronics": lambda p, t: _set_check(A.electronics_modes(t), p.electronics, set()),
    "electro_acoustic": _electro,
}  # fmt: skip
# Attributes a page-wide statement (the verified title) may supply for every option, with how to tell
# that a text states them. Not finish: a product title often names the default colour only.
_PAGE_WIDE: dict[str, Callable[[str], object]] = {
    "handedness": A.handedness,
    "size": A.sizes,
    "strings": A.string_counts,
    "pickups": A.pickup_configs,
    "electronics": A.electronics_modes,
    "electro_acoustic": A.electro_states,
}


@dataclass
class OptionCandidate:
    label: str
    amount: object  # Decimal
    currency: str | None
    price_text: str | None
    quote: str | None
    source: str  # "option" or "selected" (the main displayed price, labelled by the selected option values)


@dataclass
class Association:
    chosen: OptionCandidate | None
    reason: str
    checks: dict[str, dict[str, str]]  # label -> attribute -> result


def check_label(prof: ProductProfile, label: str, context: str, varying: set[str]) -> dict[str, str]:
    """`varying`: page-wide attributes that some option label states itself (then the title is not used)."""
    out = {}
    for name, fn in CHECKS.items():
        stated = name in _PAGE_WIDE and _PAGE_WIDE[name](label)
        if name in _PAGE_WIDE and not stated and name not in varying and context:
            out[name] = fn(prof, f"{label} | {context}")
        else:
            out[name] = fn(prof, label)
    return out


def _describe(prof: ProductProfile) -> str:
    known = [f"finish {prof.finish!r}" if prof.finish else None, f"{prof.handedness}-handed",
             f"size {sorted(prof.sizes)}" if prof.sizes else None,
             f"configuration {sorted(prof.cab_configs | prof.amp_forms)}" if prof.cab_configs or prof.amp_forms else None,
             f"pickups {sorted(prof.pickups)}" if prof.pickups else None]  # fmt: skip
    return ", ".join(k for k in known if k)


def associate(prof: ProductProfile, candidates: list[OptionCandidate], context: str) -> Association:
    """Pick the one candidate whose label matches every known variant attribute, or none."""
    varying = {name for name, stated in _PAGE_WIDE.items() for c in candidates if stated(c.label)}
    checks = {c.label: check_label(prof, c.label, context, varying) for c in candidates}
    matched: dict[str, OptionCandidate] = {}
    for c in candidates:
        if all(r not in (CONFLICT, UNKNOWN) for r in checks[c.label].values()):
            matched.setdefault(" ".join(c.label.casefold().split()), c)
    if len(matched) == 1:
        chosen = next(iter(matched.values()))
        return Association(chosen, f"price of the option {chosen.label!r}, the only option matching the catalogue "
                                   f"variant ({_describe(prof)})", checks)  # fmt: skip
    if matched:
        names = ", ".join(repr(c.label) for c in matched.values())
        return Association(None, f"several options match the catalogue variant ({names}); no price is selected", checks)
    unknown = sorted({a for r in checks.values() for a, v in r.items() if v == UNKNOWN})
    why = f"; unverifiable: {', '.join(unknown)}" if unknown else ""
    return Association(None, f"no option matches the catalogue variant ({_describe(prof)}){why}", checks)
