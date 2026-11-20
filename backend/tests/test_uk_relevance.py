"""UK relevance evidence from verified quotes (Option B; deterministic, no LLM, no network).

There is no UK category: Python verifies each quote and decides whether the verified evidence is
sufficient, contradicted or mixed, and from how many independent pages it comes."""

from datetime import UTC, datetime

import pytest

from app.agents.discovery.rules import (
    MAX_UK_QUOTES,
    assess_uk_evidence,
    physical_store_problem,
    says_no_uk_service,
    says_uk_delivery,
    verify_uk_quotes,
)
from app.agents.discovery.schemas import EvidenceItem, EvidenceKind
from app.agents.discovery.schemas import UkRelevanceEvidenceKind as K
from app.agents.discovery.schemas import UkRelevanceQuote
from app.services.text_match import MIN_QUOTE_CHARS, quote_in_evidence

NOW = datetime(2026, 11, 18, tzinfo=UTC)


def ev(url, title=None, snippet=None, canonical=None, kind=EvidenceKind.DOMAIN_PROFILE):
    return EvidenceItem(url=url, canonical_url=canonical or url, hostname=url.split("/")[2], title=title,
                        snippet=snippet, kind=kind, query_text="q", product_id=None, provider="fake",
                        provider_request_id="r", retrieved_at=NOW)  # fmt: skip


def q(url, quote, kind):
    return UkRelevanceQuote(url=url, quote=quote, kind=kind)


# Shapes based on real public pages (see the seed proposal), stored as titles/snippets.
PP = "https://www.perfectpitch.co.uk/"
PP_CONTACT = "https://www.perfectpitch.co.uk/contact-us"
UK_SHOP = [
    ev(PP, "Music Shop | Chesham | Perfect Pitch Ltd",
       "Welcome to Perfect Pitch Ltd, your go-to musical instruments shop in Chesham. PERFECT PITCH LTD, "
       "registered as a limited company in England and Wales under company number: 13898976."),
    ev(PP_CONTACT, "Luthiers | Chesham | Perfect Pitch Ltd",
       "72 The Broadway High St, Chesham, HP5 1EG 01494 774826. Prices from £49.99 with UK delivery."),
]
STORE = q(PP, "your go-to musical instruments shop in Chesham", K.UK_PHYSICAL_STORE)
REG = q(PP, "registered as a limited company in England and Wales under company number: 13898976",
        K.UK_COMPANY_REGISTRATION)
ADDR = q(PP_CONTACT, "72 The Broadway High St, Chesham, HP5 1EG", K.UK_ADDRESS)

TH = "https://www.thomann.co.uk/compinfo_imprint.html"
TH_SHIP = "https://www.thomann.co.uk/compinfo_shipping.html"
ABROAD = [
    ev(TH, "Imprint - Thomann UK", "Thomann GmbH Hans-Thomann-Strasse 1 96138 Burgebrach Germany"),
    ev(TH_SHIP, "Shipping - Thomann UK", "Free delivery to the United Kingdom on orders over £20. Prices in GBP."),
    ev("https://www.thomann.co.uk/compinfo.html", "About us",
       "The company was founded by my father Hans Thomann, senior, in 1954 in Treppendorf, Germany."),
]
OPERATOR = q(TH, "Thomann GmbH Hans-Thomann-Strasse 1 96138 Burgebrach Germany", K.NON_UK_OPERATOR)
DELIVERY = q(TH_SHIP, "Free delivery to the United Kingdom on orders over £20", K.UK_DELIVERY)
FOUNDED = q("https://www.thomann.co.uk/compinfo.html", "founded by my father Hans Thomann, senior, in 1954 in "
            "Treppendorf, Germany", K.NON_UK_OPERATOR)  # fmt: skip

US = "https://www.example-us-guitars.com/shipping"
NOT_UK = [ev(US, "Shipping policy", "We only ship within the continental United States. We do not ship to the "
                                    "United Kingdom. Prices in USD.")]  # fmt: skip
NO_SERVICE = q(US, "We do not ship to the United Kingdom", K.NO_UK_SERVICE)


# --------------------------------------------------------------------------- quote_in_evidence


def test_quote_found_verbatim_in_snippet_or_title():
    assert quote_in_evidence("your go-to musical instruments shop in Chesham", PP, UK_SHOP)
    assert quote_in_evidence("Music Shop | Chesham | Perfect Pitch Ltd", PP, UK_SHOP)


def test_quote_ignores_whitespace_and_case_only():
    assert quote_in_evidence("  YOUR GO-TO   musical\ninstruments shop in chesham ", PP, UK_SHOP)
    assert not quote_in_evidence("your go to musical instruments shop in Chesham", PP, UK_SHOP)  # hyphen dropped


def test_quote_must_come_from_the_cited_url():
    assert not quote_in_evidence("72 The Broadway High St, Chesham, HP5 1EG", PP, UK_SHOP)  # it is on /contact-us
    assert quote_in_evidence("72 The Broadway High St, Chesham, HP5 1EG", PP_CONTACT, UK_SHOP)


def test_quote_matches_the_canonical_url_form():
    items = [ev("https://shop.example.co.uk/about/", snippet="Visit our shop in Leeds, LS1 4AP",
                canonical="https://shop.example.co.uk/about")]
    assert quote_in_evidence("Visit our shop in Leeds, LS1 4AP", "https://SHOP.example.co.uk/about/#x", items)


def test_quote_cannot_be_stitched_across_title_and_snippet():
    assert not quote_in_evidence("Chesham | Perfect Pitch Ltd Welcome to Perfect Pitch Ltd", PP, UK_SHOP)


@pytest.mark.parametrize("quote", [None, "", "   ", "UK", "£49.99", "x" * (MIN_QUOTE_CHARS - 1)])
def test_empty_and_short_quotes_never_count(quote):
    assert not quote_in_evidence(quote, PP, [ev(PP, snippet="x" * 50 + " UK £49.99")])


@pytest.mark.parametrize("url", [None, "", "not a url", "https://www.other.co.uk/"])
def test_missing_invalid_or_unknown_url_never_counts(url):
    assert not quote_in_evidence("your go-to musical instruments shop in Chesham", url, UK_SHOP)


def test_invented_quote_is_not_found():
    assert not quote_in_evidence("Our flagship store on Oxford Street, London W1D 1BS", PP, UK_SHOP)


# --------------------------------------------------------------------------- UK presence


def test_uk_presence_quotes_are_sufficient_and_count_pages_not_lines():
    a = assess_uk_evidence([STORE, REG], UK_SHOP)  # two lines from the same page
    assert a.sufficient and not a.contradicted and not a.mixed_location
    assert all(x.verified and x.supports for x in a.evidence)
    assert a.independent_sources == 1  # several quotes from one page count as one source
    a = assess_uk_evidence([STORE, ADDR], UK_SHOP)  # two different pages
    assert a.sufficient and a.independent_sources == 2


def test_one_uk_presence_quote_is_sufficient_from_one_source():
    a = assess_uk_evidence([ADDR], UK_SHOP)
    assert a.sufficient and a.independent_sources == 1


def test_co_uk_domain_and_gbp_price_alone_are_never_enough():
    weak = [q(PP_CONTACT, "Prices from £49.99 with UK delivery", K.WEAK_SIGNAL),
            q(PP, "Music Shop | Chesham | Perfect Pitch Ltd", K.WEAK_SIGNAL)]  # fmt: skip
    a = assess_uk_evidence(weak, UK_SHOP)
    assert not a.sufficient and a.independent_sources == 0
    assert all(x.verified and not x.supports for x in a.evidence)


def test_price_or_domain_mislabelled_as_strong_evidence_does_not_count():
    items = [ev(PP, snippet="Boss RC-5 £189.99 perfectpitch.co.uk UK")]
    for kind in (K.UK_PHYSICAL_STORE, K.UK_ADDRESS, K.UK_COMPANY_REGISTRATION, K.UK_DELIVERY, K.NON_UK_OPERATOR):
        a = assess_uk_evidence([q(PP, "£189.99 perfectpitch.co.uk UK", kind)], items)
        assert not a.sufficient and a.evidence[0].verified and not a.evidence[0].supports
        assert "weak signal" in a.evidence[0].note


def test_invented_quote_is_ignored_and_reported():
    fake = q(PP, "Our flagship store on Oxford Street, London W1D 1BS", K.UK_PHYSICAL_STORE)
    a = assess_uk_evidence([fake], UK_SHOP)
    assert not a.sufficient and not a.evidence[0].verified and not a.evidence[0].supports
    assert any("not found in the evidence" in r for r in a.reasons)


def test_address_needs_a_uk_postcode():
    items = [ev(PP, snippet="Find us at 72 The Broadway High Street in Chesham town centre")]
    a = assess_uk_evidence([q(PP, "72 The Broadway High Street in Chesham", K.UK_ADDRESS)], items)
    assert not a.sufficient and "postcode" in a.evidence[0].note


def test_company_registration_needs_wording_and_a_number():
    items = [ev(PP, snippet="Perfect Pitch Ltd is a limited company based in England, trading since 1981")]
    a = assess_uk_evidence([q(PP, "Perfect Pitch Ltd is a limited company based in England",
                              K.UK_COMPANY_REGISTRATION)], items)  # fmt: skip
    assert not a.sufficient and "company number" in a.evidence[0].note


def test_physical_store_needs_store_wording():
    items = [ev(PP, snippet="Perfect Pitch Ltd sells guitars, amps and pedals in Chesham")]
    a = assess_uk_evidence([q(PP, "sells guitars, amps and pedals in Chesham", K.UK_PHYSICAL_STORE)], items)
    assert not a.sufficient and "physical store" in a.evidence[0].note


def test_uk_presence_with_a_based_abroad_quote_is_mixed_not_contradicted():
    # e.g. a London store run by the UK establishment of a foreign company.
    items = UK_SHOP + [ev("https://www.perfectpitch.co.uk/terms", snippet="Operated by Example Music GmbH, Germany")]
    foreign = q("https://www.perfectpitch.co.uk/terms", "Operated by Example Music GmbH, Germany", K.NON_UK_OPERATOR)
    a = assess_uk_evidence([STORE, REG, foreign], items)
    assert a.sufficient and a.mixed_location and not a.contradicted
    assert any("Mixed" in r for r in a.reasons)


def test_duplicate_quotes_count_once():
    same = STORE.model_copy(update={"quote": "YOUR GO-TO  musical instruments shop in Chesham"})
    a = assess_uk_evidence([STORE, same], UK_SHOP)
    assert a.sufficient and len(a.evidence) == 1 and a.independent_sources == 1


# --------------------------------------------------------------------------- based abroad, serves the UK


def test_based_abroad_needs_operator_and_delivery():
    a = assess_uk_evidence([OPERATOR, DELIVERY], ABROAD)
    assert a.sufficient and not a.mixed_location and a.independent_sources == 2
    a = assess_uk_evidence([OPERATOR, DELIVERY, FOUNDED], ABROAD)
    assert a.sufficient and a.independent_sources == 3


def test_overseas_retailer_needs_no_uk_domain_gbp_or_postcode():
    # An overseas shop that demonstrably serves the UK: no .uk domain, no GBP, no UK postcode.
    imprint, shipping = "https://www.musikhaus.example.de/impressum", "https://www.musikhaus.example.de/versand"
    items = [ev(imprint, "Impressum", "Musikhaus Example GmbH, Hauptstrasse 5, 80331 Munich, Germany"),
             ev(shipping, "Shipping", "We ship to the United Kingdom with DHL in 3-5 days")]  # fmt: skip
    a = assess_uk_evidence([q(imprint, "Musikhaus Example GmbH, Hauptstrasse 5, 80331 Munich, Germany",
                              K.NON_UK_OPERATOR),
                            q(shipping, "We ship to the United Kingdom with DHL", K.UK_DELIVERY)], items)  # fmt: skip
    assert a.sufficient and not a.contradicted and a.independent_sources == 2


@pytest.mark.parametrize("quotes", [[OPERATOR], [DELIVERY], [OPERATOR, FOUNDED]])
def test_based_abroad_without_both_kinds_is_insufficient(quotes):
    assert not assess_uk_evidence(quotes, ABROAD).sufficient


def test_uk_delivery_needs_delivery_and_uk_wording():
    items = [ev(TH_SHIP, snippet="Free delivery on orders over £20 worldwide, fast and tracked")]
    worldwide = q(TH_SHIP, "Free delivery on orders over £20 worldwide", K.UK_DELIVERY)
    a = assess_uk_evidence([OPERATOR, worldwide], ABROAD + items)
    assert not a.sufficient and "UK delivery" in a.evidence[1].note


def test_uk_evidence_with_a_no_uk_service_quote_is_contradicted():
    a = assess_uk_evidence([OPERATOR, DELIVERY, NO_SERVICE], ABROAD + NOT_UK)
    assert a.sufficient and a.contradicted  # both stay visible; a person decides
    assert any("Contradiction" in r for r in a.reasons)
    assert a.independent_sources == 2  # the contradicting page is not a source of UK relevance


# --------------------------------------------------------------------------- explicit "does not serve the UK"


def test_no_uk_service_needs_a_verified_quote():
    a = assess_uk_evidence([NO_SERVICE], NOT_UK)
    assert a.contradicted and not a.sufficient and a.independent_sources == 0
    a = assess_uk_evidence([], NOT_UK)
    assert not a.contradicted and not a.sufficient
    fake = q(US, "We cannot deliver to the UK or Ireland", K.NO_UK_SERVICE)
    assert not assess_uk_evidence([fake], NOT_UK).contradicted


def test_no_uk_service_needs_negated_delivery_wording():
    items = [ev(US, snippet="Our guitars are built in the United States by hand")]
    a = assess_uk_evidence([q(US, "Our guitars are built in the United States", K.NO_UK_SERVICE)], items)
    assert not a.contradicted and "explicitly say" in a.evidence[0].note


def test_no_uk_service_with_uk_delivery_is_contradicted_but_not_sufficient():
    a = assess_uk_evidence([NO_SERVICE, DELIVERY], NOT_UK + ABROAD)
    assert a.contradicted and not a.sufficient


# --------------------------------------------------------------------------- general rules


def test_no_quotes_is_insufficient_and_never_assumed():
    a = assess_uk_evidence([], UK_SHOP)
    assert (a.sufficient, a.contradicted, a.mixed_location, a.independent_sources) == (False, False, False, 0)
    assert any("Insufficient" in r for r in a.reasons)


def test_independent_sources_use_the_canonical_page():
    items = [ev("https://shop.example.co.uk/about/", snippet="Visit our shop in Leeds, LS1 4AP. Open Mon-Sat.",
                canonical="https://shop.example.co.uk/about")]
    quotes = [q("https://shop.example.co.uk/about/", "Visit our shop in Leeds, LS1 4AP", K.UK_ADDRESS),
              q("https://shop.example.co.uk/about", "Visit our shop in Leeds", K.UK_PHYSICAL_STORE)]  # fmt: skip
    a = assess_uk_evidence(quotes, items)
    assert a.sufficient and a.independent_sources == 1  # the same page, written two ways


def test_quotes_beyond_the_limit_are_ignored():
    filler = [q(PP, f"unused quote number {i:03d}", K.WEAK_SIGNAL) for i in range(MAX_UK_QUOTES)]
    a = assess_uk_evidence([*filler, STORE], UK_SHOP)
    assert not a.sufficient and len(a.evidence) == MAX_UK_QUOTES
    assert any("beyond the first" in r for r in a.reasons)


def test_assessment_is_deterministic_and_does_not_mutate_inputs():
    quotes = [STORE, REG, ADDR]
    before = [x.model_copy() for x in quotes]
    a1 = assess_uk_evidence(quotes, UK_SHOP)
    a2 = assess_uk_evidence(quotes, UK_SHOP)
    assert a1 == a2 and quotes == before


def test_verify_uk_quotes_reports_each_quote():
    out = verify_uk_quotes([STORE, q(PP, "Not on the page at all, anywhere", K.UK_ADDRESS)], UK_SHOP)
    assert [(x.verified, x.supports) for x in out] == [(True, True), (False, False)]
    assert out[1].note.startswith("not found verbatim")


def test_the_assessment_has_no_uk_category_or_confidence():
    fields = set(assess_uk_evidence([STORE], UK_SHOP).model_dump())
    assert not fields & {"relevance", "confidence", "proposed", "proposed_confidence"}


# --------------------------------------------------------------------------- A1.1: evidence-kind polarity


@pytest.mark.parametrize("text", [
    "Free delivery to the United Kingdom on orders over £20",
    "We deliver to the UK and Europe",
    "We only deliver to the UK",  # "only" restricts to the UK: still positive UK delivery
    "Next-day shipping to mainland UK",
    "Orders are dispatched daily from our Leeds warehouse to anywhere in England, Scotland and Wales",
    "We ship to the U.K. with tracked courier",
    "No minimum spend. Free delivery to the UK",  # the negation is about something else
])
def test_uk_delivery_positive(text):
    assert says_uk_delivery(text)


@pytest.mark.parametrize("text", [
    "We do not deliver to the UK",
    "We don't ship to the United Kingdom",
    "Sorry, we cannot ship orders to the UK",
    "No delivery to the UK at the moment",
    "Shipping is not available in the UK",
    "We ship worldwide except the UK.",
    "UK orders are not accepted",
    "We never ship to Great Britain",
    "We deliver to customers outside the UK",  # about other countries, not UK delivery
    "Free delivery on orders over £20 worldwide",  # no UK place
    "Guitars, amps and pedals for UK musicians",  # a UK place but no delivery wording
])
def test_uk_delivery_negative_or_ambiguous(text):
    assert not says_uk_delivery(text)


def test_selling_in_the_uk_counts_as_serving_the_uk_but_not_as_no_service():
    # Documented choice: "sell" is delivery wording, so this is positive, never a no-UK-service quote.
    assert says_uk_delivery("We sell guitars to customers across the UK")
    assert not says_no_uk_service("We sell guitars to customers across the UK")


@pytest.mark.parametrize("text", [
    "We do not ship to the United Kingdom",
    "We cannot deliver to the UK",
    "We are unable to ship orders to the UK",
    "We no longer ship to the UK following Brexit",
    "Delivery is not available in the UK",  # via 'not available in the UK'
    "No shipping to the United Kingdom",
    "We ship to all EU countries except the United Kingdom.",
    "Shipping worldwide, excluding the UK and Ireland",
    "UK customers cannot order from this store",
    "The UK is not served by this store",
])
def test_no_uk_service_explicit(text):
    assert says_no_uk_service(text)


@pytest.mark.parametrize("text", [
    "We only deliver to the UK",  # generic "only": the opposite meaning
    "We only ship within the continental United States",  # does not mention the UK
    "Not all items can be shipped",
    "We do not ship on Sundays",
    "We don't ship batteries to the UK",  # a goods restriction, not "UK not served"
    "No returns accepted after 30 days. Free UK delivery",
    "No delivery charge to the UK",  # free UK delivery, not "no delivery" (deferred regression, A2)
    "No delivery charges for UK orders over £50",
    "Free delivery on all orders except the UK Highlands",  # part of the UK, not the UK
    "We deliver to customers outside the UK",
    "UK customers are served from our Leeds warehouse",
])
def test_no_uk_service_rejects_generic_or_opposite_wording(text):
    assert not says_no_uk_service(text)


@pytest.mark.parametrize("text", [
    "your go-to musical instruments shop in Chesham",
    "Visit our showroom in St Albans",
    "Come and see us at our shop on London Road",
    "Opening hours: Monday to Saturday 10am - 5.30pm",
    "Open Mon-Sat, 9:30 - 17:00",
    "Pop into the shop and try any guitar",
    "We run on an appointment basis so we can give you the showroom all to yourself",  # by appointment is visitable
    "Visit us in store for a demo",
    "Our online store and our Guildford showroom",  # a showroom as well as the website
])
def test_physical_store_positive(text):
    assert physical_store_problem(text) is None


@pytest.mark.parametrize("text", [
    "Visit our online store for the best prices",
    "Shop online at Perfect Pitch",
    "The UK's favourite online music store",
    "We are an online-only retailer",
    "We have no physical shop; all orders are sent from our warehouse",
    "We don't have a showroom",
    "Not open to the public: warehouse only",
    "Huge range of guitars in stock",  # no shop at all
    "The best store for guitar pedals",  # "store" alone is not a place
])
def test_physical_store_negative(text):
    assert physical_store_problem(text) is not None


# Misleading labels: the LLM picks the wrong kind for a real quote.

SHIP = "https://www.example-shop.co.uk/delivery"
# One stored snippet holding several statements, so each test can quote a different sentence.
MIXED = [ev(SHIP, "Delivery information",
            "We do not deliver to the UK. Visit our online store. We only deliver to the UK.")]


def test_negative_delivery_quote_labelled_uk_delivery_does_not_count():
    a = assess_uk_evidence([OPERATOR, q(TH_SHIP, "Free delivery to the United Kingdom on orders over £20",
                                        K.UK_DELIVERY), q(SHIP, "We do not deliver to the UK", K.UK_DELIVERY)],
                           ABROAD + MIXED)  # fmt: skip
    neg = a.evidence[2]
    assert neg.verified and not neg.supports and "no negation" in neg.note
    assert a.sufficient and a.independent_sources == 2  # only the positive quote counted
    assert not a.contradicted  # a mislabelled quote never counts either way


def test_only_uk_delivery_labelled_no_uk_service_does_not_count():
    a = assess_uk_evidence([q(SHIP, "We only deliver to the UK", K.NO_UK_SERVICE)], MIXED)
    assert not a.contradicted and a.evidence[0].verified and not a.evidence[0].supports


def test_online_store_labelled_physical_store_does_not_count():
    a = assess_uk_evidence([q(SHIP, "Visit our online store", K.UK_PHYSICAL_STORE)], MIXED)
    assert not a.sufficient and "online store" in a.evidence[0].note


def test_explicit_no_uk_quote_labelled_uk_delivery_cannot_support_serving_the_uk():
    a = assess_uk_evidence([OPERATOR, q(US, "We do not ship to the United Kingdom", K.UK_DELIVERY)],
                           ABROAD + NOT_UK)  # fmt: skip
    assert not a.sufficient


def test_correctly_labelled_explicit_no_uk_quote_is_a_contradiction():
    a = assess_uk_evidence([q(SHIP, "We do not deliver to the UK", K.NO_UK_SERVICE)], MIXED)
    assert a.contradicted and not a.sufficient


def test_mislabelled_quotes_never_make_the_evidence_sufficient():
    quotes = [q(SHIP, "We do not deliver to the UK", K.NO_UK_SERVICE),
              q(SHIP, "We only deliver to the UK", K.UK_DELIVERY),
              q(SHIP, "Visit our online store", K.UK_PHYSICAL_STORE)]  # fmt: skip
    a = assess_uk_evidence(quotes, MIXED)
    assert not a.sufficient and a.contradicted  # UK delivery alone (no operator) is not sufficient
