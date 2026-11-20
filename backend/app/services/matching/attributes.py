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

# Electronics and functional variants (audit fix). Pickup layout codes match in any case ("HSS", "hss",
# "Hss", lower-case URL slugs) and are normalised to upper case. Only whole tokens count: the
# letter/digit boundaries keep codes inside words ("hhh", "class") from matching.
_PICKUPS = re.compile(r"(?<![A-Za-z0-9])(HSS|SSS|HSH|HH|SSH)(?![A-Za-z0-9])", re.IGNORECASE)
# In titles "active"/"passive" only counts next to an electronics word; in electronics specs it counts bare.
_ACTIVE_CTX = re.compile(r"\b(active|passive)[\s_-]+(?:pickups?|electronics|eq|preamp|circuit(?:ry)?|bass|humbuckers?)\b",
                         re.IGNORECASE)  # fmt: skip
_ACTIVE_BARE = re.compile(r"\b(active|passive)\b", re.IGNORECASE)
_ELECTRO = re.compile(r"\belectro[\s_-]?acoustic\b|\bacoustic[\s_-]?electric\b", re.IGNORECASE)
_NO_ELECTRO = re.compile(r"\bnon[\s_-]?electro\b|\b(?:no|without)\s+(?:pickups?|electronics|preamp)\b"
                         r"|\bacoustic[\s_-]?only\b|\bpurely\s+acoustic\b", re.IGNORECASE)  # fmt: skip
_NONE_VALUE = re.compile(r"^\s*(?:none|no|n/?a|not applicable|-+)\s*\.?$", re.IGNORECASE)

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


def pickup_configs(text: str | None) -> set[str]:
    """Pickup layout codes written in the text (HSS, SSS, HH, HSH, SSH), normalised to upper case."""
    return {m.upper() for m in _PICKUPS.findall(text or "")}


def electronics_modes(text: str | None, bare: bool = False) -> set[str]:
    """"active" / "passive" electronics. `bare=True` for electronics spec values."""
    rx = _ACTIVE_BARE if bare else _ACTIVE_CTX
    return {m.lower() for m in rx.findall(text or "")}


def electro_states(text: str | None) -> set[str]:
    """"electro" when the text says electro-acoustic, "none" when it explicitly says no electronics."""
    out = set()
    if _ELECTRO.search(text or ""):
        out.add("electro")
    if _NO_ELECTRO.search(text or ""):
        out.add("none")
    return out


def is_none_value(value: str | None) -> bool:
    return bool(_NONE_VALUE.match(value or ""))


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
    pickups: set[str] = field(default_factory=set)  # stated pickup layout codes
    electronics: set[str] = field(default_factory=set)  # stated "active" / "passive"
    # True: electro-acoustic (stated); False: an acoustic guitar the catalogue classes as non-electro; None: unknown.
    electro: bool | None = None
    # Approved model designation missing from the catalogue record (see APPROVED_DESIGNATIONS).
    designation: ApprovedDesignation | None = None


# Approved product-identity decisions: catalogue products whose model designation is part of the
# product identity but is not written in the catalogue record. Each entry is a user decision; the
# catalogue itself is unchanged. The designation is a model attribute, never a finish alias.
# Matching is exact: the designation as a whole word in a title, the URL or the extracted
# model/SKU; specifications never count. SKUs are compared exactly (case and punctuation ignored)
# and only SKUs seen on real retailer pages are listed. No fuzzy matching, no generic aliases.
@dataclass(frozen=True)
class ApprovedDesignation:
    designation: str  # e.g. "Figured"
    decided: str  # when and by whom the identity was approved
    model_skus: tuple[str, ...] = ()  # retailer SKUs verified to be this designated model
    other_model_skus: tuple[str, ...] = ()  # retailer SKUs verified to be the undesignated model


APPROVED_DESIGNATIONS: dict[str, ApprovedDesignation] = {
    # SB-028 is the Epiphone Les Paul Standard '60s Figured Left-Handed (user decision, 20 Nov 2026).
    # Figured is a separate Epiphone model (AAA figured maple top) from the plain-top Les Paul
    # Standard '60s (AA flame maple top). SKUs from the Stage 6 / 6.2 run records (19 Nov 2026):
    # EIGLP6ITNHRL (Andertons) and EIGLP6ITNH1L (Gear4music) are the Figured LH; EILS6ITNH1
    # (Gear4music secondhand) is the plain-top model.
    "SB-028": ApprovedDesignation(
        designation="Figured", decided="User decision, 20 Nov 2026 (Stage 6.3)",
        model_skus=("EIGLP6ITNHRL", "EIGLP6ITNH1L"), other_model_skus=("EILS6ITNH1",),
    ),
}


def designation_in(designation: str, text: str | None) -> bool:
    """The designation as a whole word ("Figured" in "Standard '60s Figured Left Handed")."""
    return bool(text) and re.search(rf"(?<![a-z0-9]){re.escape(designation.casefold())}(?![a-z0-9])",
                                    text.casefold()) is not None  # fmt: skip


def sku_in(skus: tuple[str, ...], text: str | None) -> set[str]:
    """Listed SKUs written in the text as a whole token ("EILS6ITNH1", "eils6itnh1", "EI-LS6ITNH1").

    A token is a run of letters/digits joined by "-", "_" or "."; it is compared with case and those
    joiners removed. "EILS6ITNH1L" is a different token from "EILS6ITNH1" and never matches it."""
    tokens = {compact(t) for t in re.findall(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*", text or "")}
    return {s for s in skus if compact(s) in tokens}


# Controlled finish aliases (Stage 6.1): catalogue finish -> other spellings verified on real retailer
# pages. Matching is exact on the normalised phrase (case, spacing and punctuation ignored); there is no
# fuzzy matching. Add an alias only with evidence, and note where it was seen.
FINISH_ALIASES: dict[str, tuple[str, ...]] = {
    # "ice tea": Gear4music "Epiphone Les Paul Standard 60s, Ice Tea" (Stage 6, 19 Nov 2026).
    # "iced tea": Wildwire "... Figured Left-Handed in Iced Tea with Gig Bag" (Stage 7, 20 Nov 2026; user-approved).
    "iced tea burst": ("ice tea", "iced tea"),
}


def finish_key(text: str | None) -> str:
    """Normalised finish phrase: casefolded, punctuation as spaces, whitespace collapsed."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").casefold()).split())


def finish_names(finish: str) -> list[str]:
    key = finish_key(finish)
    return [key, *FINISH_ALIASES.get(key, ())]


def finish_in(finish: str, text: str | None) -> bool:
    """The catalogue finish, or one of its controlled aliases, as a whole phrase in the text."""
    if mentions_model(finish, text):
        return True
    padded = f" {finish_key(text)} "
    return any(name and f" {name} " in padded for name in finish_names(finish))


# Colour words that describe a part, not the body finish ("Gold Hardware", "Cream Binding"), and sheen
# words that qualify a finish rather than name another one ("Natural Satin"). Stage 6.1.1.
_PART_COLOUR = re.compile(
    r"\b[a-z]+ (?:hardware|pickguard|scratch ?plate|binding|fingerboard|fretboard|inlays?|knobs?|tuners?|headstock"
    r"|case|gig bag|bag|strap)\b"
)
_SHEEN = frozenset({"satin", "gloss", "matte", "metallic"})


def other_finish_colours(finish: str, text: str | None, ignore: set[str] = frozenset()) -> set[str]:
    """Colour words left in the text after removing the catalogue finish and its aliases.

    Non-empty means the text names another finish as well ("Ice Tea / Ebony"): mixed evidence,
    never a confirmed finish. Part colours, sheen words and `ignore` (identity words) are skipped.
    """
    padded = f" {finish_key(text)} "
    for name in finish_names(finish):
        while name and f" {name} " in padded:
            padded = padded.replace(f" {name} ", " ")
    padded = _PART_COLOUR.sub(" ", padded)
    return colour_words(padded) - _SHEEN - set(ignore)


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
        pickups=pickup_configs(f"{p.product_name} {p.manufacturer_model} {p.specifications or ''}"),
        electronics=electronics_modes(f"{p.product_name} {p.manufacturer_model} {p.subcategory}")
        | electronics_modes(p.specifications, bare=True),
        electro=_electro(p, text), designation=APPROVED_DESIGNATIONS.get(p.product_id),
    )  # fmt: skip


def _electro(p: StompboxProduct, text: str) -> bool | None:
    """From the catalogue only: an "Electro-acoustic" product is electro; one whose subcategory is
    an acoustic guitar without electro wording is not; anything else is unknown."""
    if _ELECTRO.search(text):
        return True
    if p.category == "Guitars" and re.search(r"\bacoustic\b", p.subcategory, re.IGNORECASE):
        return False
    return None
