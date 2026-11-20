"""Deterministic availability normalisation (approved decisions P-5, U-3)."""

import pytest

from app.schemas.common import AvailabilityStatus as A
from app.services.availability import normalize_availability


def status(text, page="", proposal=None, verified=True, contradicted=False):
    return normalize_availability(text, verified=verified, page_text=page, proposal=proposal,
                                  contradicted=contradicted)[0]  # fmt: skip


@pytest.mark.parametrize("text,expected", [
    ("In stock", A.IN_STOCK),
    ("In Stock - dispatched today", A.IN_STOCK),
    ("Available now", A.IN_STOCK),
    ("Ready to ship", A.IN_STOCK),
    ("Out of stock", A.OUT_OF_STOCK),
    ("Sold out", A.OUT_OF_STOCK),
    ("Not in stock", A.OUT_OF_STOCK),
    ("Currently unavailable", A.OUT_OF_STOCK),
    ("Pre-order", A.PREORDER),
    ("Available to pre-order", A.PREORDER),
    ("Preorder now", A.PREORDER),
    ("On backorder", A.BACKORDER),
    ("Back order", A.BACKORDER),
    ("Awaiting stock", A.BACKORDER),
    ("In stock soon", A.BACKORDER),
    ("Discontinued", A.DISCONTINUED),
    ("This product is no longer available", A.DISCONTINUED),
])  # fmt: skip
def test_uk_retail_wordings_map_to_the_six_statuses(text, expected):
    assert status(text) is expected


@pytest.mark.parametrize("text", ["Only 2 left", "Low stock", "Limited stock"])
def test_low_stock_is_in_stock_only_when_the_page_shows_it_can_be_bought_now(text):
    assert status(text, page=f"Boss RC-5 £189 {text} Add to basket") is A.IN_STOCK
    assert status(f"{text} - in stock") is A.IN_STOCK
    assert status(text, page=f"Boss RC-5 £189 {text}") is A.UNKNOWN  # no purchasability signal


@pytest.mark.parametrize("kwargs,reason", [
    (dict(text=None), "No verified"),  # missing
    (dict(text="In stock", verified=False), "No verified"),  # quote not in the page
    (dict(text="Call for availability"), "not a recognised"),  # unrecognised
    (dict(text="In stock / Out of stock"), "mixes several"),  # conflicting wording
    (dict(text="In stock", contradicted=True), "contradictory"),  # verified LLM 2 conflict issue
    (dict(text="In stock", proposal=A.OUT_OF_STOCK), "LLM 2 proposed"),  # disagreement
    (dict(text="In stock", proposal=A.UNKNOWN), "LLM 2 proposed"),  # LLM 2 unsure: never upgraded
])  # fmt: skip
def test_missing_ambiguous_conflicting_or_unverified_evidence_is_unknown(kwargs, reason):
    text = kwargs.pop("text")
    st, why = normalize_availability(text, verified=kwargs.pop("verified", True), **kwargs)
    assert st is A.UNKNOWN and reason in why


def test_agreeing_proposal_keeps_the_mapped_status():
    assert status("Out of stock", proposal=A.OUT_OF_STOCK) is A.OUT_OF_STOCK


def test_never_infers_unavailable_from_absence():
    # No text at all (e.g. no LLM, or search found nothing) is UNKNOWN, never OUT_OF_STOCK.
    assert status(None) is A.UNKNOWN and status("", page="Out of stock elsewhere") is A.UNKNOWN


# --------------------------------------------------------------------------- Stage 6.3.1: geography

GEO = [
    "Unavailable for delivery to your country",
    "This product is currently unavailable for delivery to your country",  # Gear4music, Stage 6.2
    "Not available for delivery to your region",
    "We cannot ship this item to your location",
    "This item can't be delivered to the selected country",
    "Sorry, this product is not available in your country",
    "We do not ship this product to the United Kingdom",
    "Not available for delivery",
    "Out of stock - unavailable for delivery to your country",  # a restriction always wins
    "In stock, but unavailable for delivery to your country",
]


@pytest.mark.parametrize("text", GEO)
def test_geographic_or_delivery_restrictions_are_unknown(text):
    st, why = normalize_availability(text, verified=True)
    assert st is A.UNKNOWN and "geographic or delivery restriction" in why


@pytest.mark.parametrize("proposal", [A.OUT_OF_STOCK, A.IN_STOCK, A.UNKNOWN, None])
def test_no_llm2_proposal_overrides_a_verified_restriction(proposal):
    assert status("This product is currently unavailable for delivery to your country", proposal=proposal) is A.UNKNOWN


@pytest.mark.parametrize("text,expected", [
    ("Out of stock", A.OUT_OF_STOCK),
    ("Sold out", A.OUT_OF_STOCK),
    ("Currently unavailable", A.OUT_OF_STOCK),  # no geographic context: unchanged
    ("This product is currently not available for purchase", A.OUT_OF_STOCK),  # Gear4music, Stage 6.2
    ("In stock", A.IN_STOCK),
    ("Pre-order", A.PREORDER),
    ("On backorder", A.BACKORDER),
    ("Discontinued", A.DISCONTINUED),
])  # fmt: skip
def test_stock_wording_without_restrictions_is_unchanged(text, expected):
    page = f"Boss RC-5 £189 {text}. Free UK delivery. We deliver to over 100 countries. No delivery charge in the UK."
    assert status(text, page=page) is expected


@pytest.mark.parametrize("text", ["Currently unavailable", "This product is currently not available for purchase",
                                  "Not available"])  # fmt: skip
def test_generic_unavailability_on_a_page_with_a_restriction_is_unknown(text):
    page = f"Boss RC-5 {text}. This product is currently unavailable for delivery to your country."
    st, why = normalize_availability(text, verified=True, page_text=page)
    assert st is A.UNKNOWN and "geographic or delivery restriction" in why


@pytest.mark.parametrize("text", ["Out of stock", "Sold out", "Not in stock"])
def test_explicit_out_of_stock_survives_a_page_restriction_and_an_unsure_llm2(text):
    page = f"{text}. Not available for delivery to your region."
    assert status(text, page=page) is A.OUT_OF_STOCK
    st, why = normalize_availability(text, verified=True, proposal=A.UNKNOWN)
    assert st is A.OUT_OF_STOCK and "does not override" in why
    assert status(text, proposal=A.IN_STOCK) is A.UNKNOWN  # a contrary proposal still means review


def test_llm2_unknown_still_leaves_generic_unavailability_unknown():
    assert status("Currently unavailable", proposal=A.UNKNOWN) is A.UNKNOWN
    assert status("This product is currently not available for purchase", proposal=A.UNKNOWN) is A.UNKNOWN


def test_unverified_restriction_text_is_unknown_for_the_usual_reason():
    st, why = normalize_availability("Unavailable for delivery to your country", verified=False)
    assert st is A.UNKNOWN and "No verified" in why
