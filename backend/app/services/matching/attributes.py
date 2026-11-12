"""Deterministic attribute detection for matching (Phase 6).

Everything here reads short identity texts (titles, URL paths, extracted
fields), never long page bodies, and returns what is literally written. The
Stompbox side is profiled from the catalogue row; nothing is inferred beyond
what the name, model, subcategory and specifications say.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.schemas.catalogue import StompboxProduct
from app.services.text_match import mentions_model

# --------------------------------------------------------------------------- vocabularies

COLOUR_WORDS = frozenset("""
black white red blue green yellow orange purple pink gold golden silver grey gray brown natural sunburst burst
starburst cherry burgundy graphite ebony walnut mahogany ivory cream sapphire amethyst turquoise teal metallic satin
gloss matte tobacco honey amber ice sonic seafoam olive copper bronze champagne poppy ember midnight snow
""".split())  # fmt: skip
# Marketing phrases containing colour words that are not finishes.
_NOT_FINISH = re.compile(r"\bblack\s+friday\b|\bcyber\s+monday\b|\bwhite\s+label\b", re.IGNORECASE)

_LEFT = re.compile(r"(?<![a-z])(?:left[\s_-]?hand(?:ed)?|lefty|lh)(?![a-z])", re.IGNORECASE)
_RIGHT = re.compile(r"(?<![a-z])right[\s_-]?hand(?:ed)?(?![a-z])", re.IGNORECASE)
_STRINGS = re.compile(r"(?<![0-9a-z])(\d{1,2})[\s_-]?strings?(?![a-z])", re.IGNORECASE)
_SIZE = re.compile(r"(?<![0-9/])(1/2|3/4|7/8|4/4)(?:[\s_-]?size)?(?![0-9/])|\bfull[\s_-]size\b", re.IGNORECASE)
_WATTS = re.compile(r"(?<![0-9a-z.])(\d{1,3})[\s_-]?(?:w|watts?)(?![a-z])", re.IGNORECASE)
# Speaker layouts: 1/2/4/8 speakers of 8/10/12/15 inches ("2x12"). Narrow on purpose:
# retailer product codes such as Gear4music "7X8" must not read as a cabinet.
_CAB = re.compile(r"(?<![0-9a-z])([1248])\s?x\s?(8|10|12|15)(?![0-9a-z])", re.IGNORECASE)
_AMP_FORM = re.compile(r"\b(head|combo|cab|cabinet|stack)\b", re.IGNORECASE)
_BUNDLE = re.compile(r"\b(bundle|pack|kit|package|starter set)\b|\s\+\s", re.IGNORECASE)

_ROMAN = {"ii": 2, "iii": 3, "iv": 4, "vi": 6, "vii": 7, "viii": 8}
_GEN = re.compile(
    r"(?<![0-9a-z])(?:mk\.?\s?(?P<mk>[ivx]{1,4}|\d{1,2})|v(?P<v>\d{1,2})|gen(?:eration)?\.?\s?(?P<gen>\d{1,2})"
    r"|(?P<roman>ii|iii|iv|vi|vii|viii))(?![0-9a-z])",
    re.IGNORECASE,
)
_EDITION = re.compile(r"\b(\d{1,3}(?:st|nd|rd|th)\s+(?:anniversary\s+)?edition|reissue|limited\s+edition)\b",
                      re.IGNORECASE)  # fmt: skip

_CONDITION = [
    ("USED", re.compile(r"(?<![a-z])(?:used|second[\s_-]?hand|pre[\s_-]?owned|preloved|pre[\s_-]?loved)(?![a-z])", re.I)),
    ("REFURBISHED", re.compile(
        r"(?<![a-z])(?:refurb(?:ished)?|b[\s_-]?stock|ex[\s_-]?demo|ex[\s_-]?display|open[\s_-]?box|nearly[\s_-]?new"
        r"|graded|renewed|blemished|returned)(?![a-z])", re.I)),
    ("NEW", re.compile(r"(?<![a-z])(?:brand[\s_-]?new|new\s+in\s+box|condition:?\s*new)(?![a-z])", re.I)),
]  # fmt: skip

_STOP = frozenset("""guitar guitars effect effects pedal pedals electric acoustic the and with for series amp amplifier
audio multi bass""".split())  # fmt: skip


def words(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").casefold())


def colour_words(text: str | None) -> set[str]:
    return {w for w in words(_NOT_FINISH.sub(" ", text or "")) if w in COLOUR_WORDS}


def handedness(text: str | None) -> str | None:
    if _LEFT.search(text or ""):
        return "left"
    if _RIGHT.search(text or ""):
        return "right"
    return None


def string_counts(text: str | None) -> set[str]:
    return {m for m in _STRINGS.findall(text or "")}


def sizes(text: str | None) -> set[str]:
    out = set()
    for m in _SIZE.finditer(text or ""):
        out.add((m.group(1) or "full").lower())
    return out


def wattages(text: str | None) -> set[str]:
    return set(_WATTS.findall(text or ""))


def cab_configs(text: str | None) -> set[str]:
    return {f"{a}x{b}" for a, b in _CAB.findall(text or "")}


def amp_forms(text: str | None) -> set[str]:
    return {("cabinet" if m.lower() in ("cab", "cabinet") else m.lower()) for m in _AMP_FORM.findall(text or "")}


def is_bundle(text: str | None) -> bool:
    return bool(_BUNDLE.search(text or ""))


def generations(text: str | None) -> set[int]:
    out = set()
    for m in _GEN.finditer(text or ""):
        raw = (m.group("mk") or m.group("v") or m.group("gen") or m.group("roman") or "").lower()
        out.add(int(raw) if raw.isdigit() else _ROMAN.get(raw, {"i": 1, "v": 5, "x": 10}.get(raw, 0)))
    out.discard(0)
    return out


def editions(text: str | None) -> set[str]:
    return {" ".join(m.lower().split()) for m in _EDITION.findall(text or "")}


def condition_of(text: str | None) -> tuple[str, str] | None:
    """(condition, matched text) for the first condition marker, used/refurbished before new."""
    for name, rx in _CONDITION:
        m = rx.search(text or "")
        if m:
            return name, m.group(0)
    return None


def type_stems(text: str | None) -> set[str]:
    """Product-type stems (first 4 letters of significant words), e.g. 'loop', 'fuzz'."""
    return {w[:4] for w in words(text) if len(w) >= 4 and w not in _STOP and not w.isdigit()}


# --------------------------------------------------------------------------- model identity


def compact(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", text.casefold())


def strip_variant_tokens(model: str) -> str:
    """Model without handedness and generation tokens: "Les Paul Standard '60s LH" -> "Les Paul Standard '60s"."""
    core = _LEFT.sub(" ", model)
    core = _GEN.sub(" ", core)
    core = _JOINERS.sub(" ", _CAB.sub(" ", core))  # "Head & 2x12" -> "Head" (layout is a configuration variant)
    return " ".join(core.split())


_CODE = re.compile(r"^([a-z]{1,6})(\d{1,4})([a-z]{0,4})$")


def model_family(model: str) -> str | None:
    """Alphabetic prefix of a code-like model ('RC-5' -> 'rc', 'BAA2CE' -> 'baa'), else None."""
    m = _CODE.match(compact(strip_variant_tokens(model)))
    return m.group(1) if m else None


def is_code_like(model: str) -> bool:
    """A single model code mixing letters and digits ("BAT4CEL", "WST-905Li"), not a descriptive name."""
    c = compact(model)
    return " " not in model.strip() and bool(re.search(r"[a-z]", c) and re.search(r"\d", c))


def sibling_models(model: str, text: str | None) -> set[str]:
    """Other models of the same code family in the text, e.g. 'rc500' for 'RC-5'."""
    family = model_family(model)
    if not family or not text:
        return set()
    own = compact(strip_variant_tokens(model))
    found = set()
    for m in re.finditer(rf"(?<![0-9a-z]){re.escape(family)}[\s\-_.]?\d{{1,4}}[a-z0-9]{{0,4}}(?![0-9a-z])",
                         text, re.IGNORECASE):  # fmt: skip
        c = compact(m.group(0))
        # "G250" inside "G250 SE" is the start of our own model, not a sibling.
        if c != own and not mentions_model(model, _clean(text[m.start(): m.start() + len(model) + 8])):
            found.add(c)
    return found


_JOINERS = re.compile(r"[&+,:;()|\[\]]")


def _clean(text: str) -> str:
    """Joiners such as "&" become spaces so "Head & 2x12" reads like "Head 2x12"."""
    return _JOINERS.sub(" ", text)


def model_in(model: str, text: str | None) -> bool:
    """Exact model identity in text: the full model, or its core plus the same generation."""
    if not text:
        return False
    text, model = _clean(text), _clean(model)
    if mentions_model(model, text):
        return True
    core = strip_variant_tokens(model)
    gens = generations(model)
    if core and compact(core) != compact(model) and mentions_model(core, text):
        return not gens or bool(gens & generations(text))
    return False


def core_in(model: str, text: str | None) -> bool:
    core = strip_variant_tokens(_clean(model))
    return bool(core and text and mentions_model(core, _clean(text)))


def brand_in(brand: str, text: str | None) -> bool:
    """Full brand, or its first word when that word is distinctive (>= 4 chars)."""
    if not text:
        return False
    if mentions_model(brand, text):
        return True
    first = brand.split()[0]
    return len(brand.split()) > 1 and len(first) >= 4 and mentions_model(first, text)


# --------------------------------------------------------------------------- Stompbox profile


@dataclass
class ProductProfile:
    product: StompboxProduct
    identity_text: str  # name + model + subcategory + specifications
    finish: str | None
    finish_colours: set[str]
    handedness: str  # "left" or "right" (right = not stated as left)
    strings: set[str]  # stated string count
    strings_default: str | None  # 6 for guitars, 4 for basses, None for non-instruments
    sizes: set[str]
    wattages: set[str]
    cab_configs: set[str]
    amp_forms: set[str]
    bundle: bool
    generations: set[int]
    editions: set[str]
    type_stems: set[str]
    name_words: set[str] = field(default_factory=set)  # words that are part of the identity, not a finish


def _finish(name: str) -> str | None:
    """Finish = last comma segment of the name when it names a colour (e.g. 'Iced Tea Burst')."""
    if "," not in name:
        m = re.search(r"\bin\s+([A-Z][\w ]+)$", name)  # "... in Shadow Mist"
        seg = m.group(1) if m else None
    else:
        seg = name.rsplit(",", 1)[1]
    if not seg:
        return None
    seg = re.split(r"\bwith\b", seg, flags=re.IGNORECASE)[0].strip()
    if handedness(seg) or wattages(seg) or not colour_words(seg):
        return None
    return seg


def profile(p: StompboxProduct) -> ProductProfile:
    text = " ".join(x for x in (p.product_name, p.manufacturer_model, p.subcategory, p.specifications or "") if x)
    finish = _finish(p.product_name)
    name_wo_finish = p.product_name.replace(finish, " ") if finish else p.product_name
    return ProductProfile(
        product=p, identity_text=text, finish=finish, finish_colours=colour_words(finish),
        handedness=handedness(f"{p.product_name} {p.manufacturer_model} {p.subcategory}") or "right",
        strings=string_counts(f"{p.product_name} {p.subcategory}"), sizes=sizes(p.product_name),
        strings_default=(None if p.category != "Guitars" else
                         "4" if re.search(r"\bbass\b", f"{p.subcategory} {p.product_name}", re.I) else "6"),
        wattages=wattages(p.product_name), cab_configs=cab_configs(f"{p.product_name} {p.manufacturer_model}"),
        amp_forms=amp_forms(f"{p.product_name} {p.manufacturer_model} {p.subcategory}"),
        bundle=is_bundle(p.product_name) or bool(re.search(r"\bwith\b.*\b(bag|case)\b", p.product_name, re.I)),
        generations=generations(f"{p.manufacturer_model} {p.product_name}"),
        editions=editions(p.product_name), type_stems=type_stems(f"{p.subcategory} {p.product_name}"),
        name_words=set(words(f"{p.brand} {p.manufacturer_model} {name_wo_finish}")),
    )  # fmt: skip
