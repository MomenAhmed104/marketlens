"""Benchmark product identity specs.

The catalogue's `manufacturer_model` sometimes mixes the base model with a
variant (e.g. "Les Paul Standard '60s LH"). For the benchmark, each sample
product is described as a base model plus variant rules taken from the
catalogue's product name/SKU. These specs are benchmark fixtures only; the
catalogue itself is not modified.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BenchmarkProduct:
    product_id: str
    base_model: str
    # Each group is a regex; ALL groups must match for an exact identity.
    required_variants: tuple[str, ...] = ()
    # Any match means a different product (e.g. head vs head-and-cab stack).
    forbidden: tuple[str, ...] = ()
    # Any match means identity cannot be decided automatically (REVIEW).
    review_terms: tuple[str, ...] = ()
    # Expected but not always written by retailers: absent -> REVIEW, not EXACT.
    review_if_missing: tuple[str, ...] = ()
    # Regex for the brand when the catalogue brand string is not how retailers
    # write it. Default: the catalogue brand with flexible separators.
    brand_pattern: str | None = None
    # Extra words appended to "<brand> <base_model>" in search queries.
    query_extra: str = ""
    rationale: str = ""
    catalogue_source: str = ""  # which catalogue field the variant rules come from
    tags: tuple[str, ...] = field(default_factory=tuple)


LEFT_HANDED = r"left[\s-]?hand(?:ed)?|\bLH\b|lefty"

SAMPLE: tuple[BenchmarkProduct, ...] = (
    BenchmarkProduct("SB-001", "RC-5",
        rationale="Major brand, widely stocked; must not match RC-500/RC-505"),
    BenchmarkProduct("SB-081", "Helix LT",
        rationale="High-value multi-effects; siblings Helix Floor/Stomp"),
    BenchmarkProduct("SB-069", "3 Series Reverb", forbidden=(r"\bhall\b",),
        rationale="Near-duplicate of '3 Series Hall Reverb'"),
    BenchmarkProduct("SB-075", "Whammy DT",
        rationale="Siblings Whammy V/Ricochet/MonoNeon"),
    BenchmarkProduct("SB-013", "Ensemble King", rationale="Budget brand"),
    BenchmarkProduct("SB-055", "OMP-1", brand_pattern=r"\bemma\b",
        rationale="Niche boutique brand ('Emma Electronic' in the product name)"),
    BenchmarkProduct(
        "SB-028", "Les Paul Standard 60s",
        required_variants=(LEFT_HANDED, r"iced[\s-]?tea|\bITB\b"),
        # Stompbox's own product page describes it as the "Les Paul Standard 60s
        # Figured" (retrieved 2026-11-10); the catalogue name omits "Figured".
        review_if_missing=(r"\bfig(?:ured)?\b|\bF\b",),
        query_extra="Left Handed Iced Tea",
        rationale="Left-handed AND finish variant (Iced Tea Burst); Figured model",
        catalogue_source=("product_name: 'Left-Handed ... Iced Tea Burst'; model 'Les Paul Standard '60s LH'; "
                          "Stompbox page text: 'Les Paul Standard 60s Figured'"),
        tags=("left-handed", "finish"),
    ),
    BenchmarkProduct(
        "SB-021", "G250 SE",
        required_variants=(r"vivid[\s-]?burgundy|\bVVB\b",),
        query_extra="Vivid Burgundy",
        rationale="Finish-specific SKU",
        catalogue_source="product_name: 'Vivid Burgundy'; stompbox_sku G250SEVVB",
        tags=("finish",),
    ),
    BenchmarkProduct(
        "SB-035", "BTB705LM",
        required_variants=(r"cosmic[\s-]?blue",),
        query_extra="Cosmic Blue Starburst",
        rationale="5-string bass, alphanumeric code, finish variant",
        catalogue_source="product_name: 'Cosmic Blue Starburst Low Gloss'",
        tags=("finish",),
    ),
    BenchmarkProduct(
        "SB-044", "BAT4CEL",
        rationale="Niche left-handed electro-acoustic; the 'L' in the code is the LH variant",
        catalogue_source="product_name: 'Left Handed'; model BAT4CEL (BAT4CE is right-handed)",
        tags=("left-handed",),
    ),
    BenchmarkProduct("SB-049", "Mighty 20 MKII", rationale="Generation suffix"),
    BenchmarkProduct(
        "SB-047", "St. James 50 6L6",
        required_variants=(r"\bhead\b",),
        forbidden=(r"2\s?x\s?12|\b212\b|\bcab(?:inet)?\b|\bstack\b|\bcombo\b",),
        query_extra="Head",
        rationale="Head vs head-and-cab configuration",
        catalogue_source="product_name: 'Amplifier Head'",
        tags=("configuration",),
    ),
    BenchmarkProduct("SB-091", "RCT-4", rationale="Low-price accessory"),
)  # fmt: skip

SAMPLE_BY_ID = {p.product_id: p for p in SAMPLE}
