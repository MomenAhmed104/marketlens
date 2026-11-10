from decimal import Decimal

import pytest

from app.benchmark.metrics import (
    Identity,
    attribute_price,
    classify_identity,
    gbp_prices,
    mentions_model,
    model_pattern,
    page_type,
    percentile,
    url_text,
)
from app.benchmark.products import SAMPLE, SAMPLE_BY_ID
from app.benchmark.search_benchmark import (
    ResultRow,
    RetrievalRecord,
    SearchRecord,
    build_pool,
    summarise,
)


@pytest.mark.parametrize(
    "model, text, expected",
    [
        ("RC-5", "Boss RC-5 Loop Station", True),
        ("RC-5", "boss-rc5-loop-station", True),
        ("RC-5", "Boss RC 5", True),
        ("RC-5", "Boss RC-500 Loop Station", False),
        ("RC-5", "Boss RC-505mkII", False),
        ("3 Series Reverb", "JHS 3 Series Hall Reverb", False),
        ("3 Series Reverb", "JHS 3 Series Reverb Pedal", True),
        ("Whammy DT", "DigiTech Whammy V", False),
        ("Mighty 20 MKII", "NUX Mighty 20 MK II", True),
        ("Mighty 20 MKII", "NUX Mighty 20", False),
        ("Les Paul Standard 60s", "Epiphone Les Paul Standard '60s LH", True),
        ("G250 SE", "Cort G250SE Vintage Burst", True),
    ],
)
def test_model_matching(model, text, expected):
    assert bool(model_pattern(model).search(text)) is expected


def test_mentions_model_uses_url_slug():
    assert mentions_model("BTB705LM", None, url_text("https://x.co.uk/ibanez-btb705lm-bass%20guitar"))
    assert not mentions_model("BTB705LM", None, "")


def test_gbp_prices():
    text = "Was £1,299.00 now £999 | GBP 49.50 | £ 5.99 | budget £2k | $100 | €90"
    assert gbp_prices(text) == [Decimal("1299.00"), Decimal("999.00"), Decimal("49.50"), Decimal("5.99")]
    assert gbp_prices(None) == [] and gbp_prices("no prices") == []


def test_percentile():
    assert percentile([], 50) is None
    assert percentile([5.0], 95) == 5.0
    assert percentile([1, 2, 3, 4], 50) == 2
    assert percentile(list(range(1, 101)), 95) == 95


# --------------------------------------------------------------------------- identity


@pytest.mark.parametrize(
    "spec_id, title, url, expected",
    [
        # SB-028: left-handed AND Iced Tea finish required; "Figured" expected
        ("SB-028", "Epiphone Les Paul Standard 60s Figured Left Handed Iced Tea Burst", "https://x.co.uk/p", Identity.EXACT),
        ("SB-028", None, "https://www.thomann.co.uk/epiphone_les_paul_standard_60s_f_itb_lh.htm", Identity.EXACT),
        ("SB-028", "Epiphone Les Paul Standard 60s, Left-Handed, Iced Tea Burst", "https://x.co.uk/p", Identity.REVIEW),
        ("SB-028", None, "https://www.thomann.co.uk/epiphone_les_paul_standard_60s_itb_lh.htm", Identity.REVIEW),
        ("SB-028", "Epiphone Les Paul Standard 60s Left Handed, Ebony", "https://x.co.uk/p", Identity.VARIANT_MISMATCH),
        ("SB-028", "Epiphone Les Paul Standard 60s Ebony LH", "https://www.thomann.co.uk/epiphone_les_paul_standard_60s_ebony_lh.htm", Identity.VARIANT_MISMATCH),
        ("SB-028", "Epiphone Les Paul Standard 60s Figured Iced Tea", "https://x.co.uk/p", Identity.VARIANT_MISMATCH),
        ("SB-028", "Epiphone Les Paul Standard 60s Left Handed ...", "https://x.co.uk/p", Identity.REVIEW),
        ("SB-028", "Epiphone 2022 Les Paul Standard 60s Iced Tea Left Handed (Pre-Owned)", "https://x.co.uk/p", Identity.CONDITION_MISMATCH),
        ("SB-028", "Epiphone Les Paul Special", "https://x.co.uk/p", Identity.NO_MATCH),
        # Others
        ("SB-001", "Boss RC-5 Loop Station", "https://x.co.uk/p", Identity.EXACT),
        ("SB-001", "BOSS RC-5 Loop Station (Pre-Owned) #J5P0552", "https://x.co.uk/p", Identity.CONDITION_MISMATCH),
        ("SB-001", "Boss RC-500 Loop Station", "https://x.co.uk/p", Identity.NO_MATCH),
        ("SB-001", None, "https://www.gear4music.com/G4M/Boss-RC-5-Loop-Station-Secondhand/70E5", Identity.CONDITION_MISMATCH),
        ("SB-069", "JHS 3 Series Hall Reverb", "https://x.co.uk/p", Identity.NO_MATCH),
        ("SB-047", "Blackstar St. James 50 6L6 Head", "https://x.co.uk/p", Identity.EXACT),
        ("SB-047", "Blackstar St James 50 6L6 Head & 2x12 Cab", "https://x.co.uk/p", Identity.VARIANT_MISMATCH),
        ("SB-047", "Blackstar St. James 50 6L6 Combo", "https://x.co.uk/p", Identity.VARIANT_MISMATCH),
        ("SB-021", "Cort G250 SE Vivid Burgundy", "https://x.co.uk/p", Identity.EXACT),
        ("SB-021", "Cort G250 SE Olive Dark Green", "https://x.co.uk/p", Identity.VARIANT_MISMATCH),
        ("SB-044", "Bromo Tahoma BAT4CEL Left Handed", "https://x.co.uk/p", Identity.EXACT),
        ("SB-044", "Bromo Tahoma BAT4CE", "https://x.co.uk/p", Identity.NO_MATCH),
        ("SB-035", "Ibanez BTB 705LM Cosmic Blue Starburst Low Gloss", "https://x.co.uk/p", Identity.EXACT),
    ],
)  # fmt: skip
def test_classify_identity(spec_id, title, url, expected):
    assert classify_identity(SAMPLE_BY_ID[spec_id], title, url) == expected


def test_brand_is_required_for_exact(catalogue):
    lp = SAMPLE_BY_ID["SB-028"]
    gibson = "Gibson Les Paul Standard 60s Figured Left Handed, Iced Tea"
    epi = "Epiphone Inspired by Gibson Les Paul Standard 60s Figured Left Handed Iced Tea"
    assert classify_identity(lp, gibson, "https://x.co.uk/p", "Epiphone") == Identity.REVIEW
    assert classify_identity(lp, epi, "https://x.co.uk/p", "Epiphone") == Identity.EXACT
    # brand in the URL slug is enough
    assert classify_identity(SAMPLE_BY_ID["SB-001"], None, "https://x.co.uk/boss-rc-5", "Boss") == Identity.EXACT
    assert classify_identity(SAMPLE_BY_ID["SB-001"], "RC-5 Loop Station", "https://x.co.uk/p", "Boss") == Identity.REVIEW
    # brand_pattern override: catalogue says "Emma Electronics", retailers say "Emma Electronic"
    omp = SAMPLE_BY_ID["SB-055"]
    assert classify_identity(omp, "Emma Electronic OMP-1 OnoMATOpoeia", "https://x.co.uk/p",
                             catalogue.products["SB-055"].brand) == Identity.EXACT  # fmt: skip


def test_every_sample_product_has_a_spec(catalogue):
    assert len(SAMPLE) == 13 == len(SAMPLE_BY_ID)
    assert all(pid in catalogue.products for pid in SAMPLE_BY_ID)


@pytest.mark.parametrize(
    "host, url, expected",
    [
        ("guitarguitar.co.uk", "https://www.guitarguitar.co.uk/product/200930362578025--boss-rc-5-loop-station", "product"),
        ("guitarguitar.co.uk", "https://www.guitarguitar.co.uk/boss/pedals/looper", "category"),
        ("guitarguitar.co.uk", "https://www.guitarguitar.co.uk/news/142252", "content"),
        ("thomann.co.uk", "https://www.thomann.co.uk/boss_rc_5_loop_station.htm", "product"),
        ("thomann.co.uk", "https://www.thomann.co.uk/boss_looper.html", "category"),
        ("gear4music.com", "https://www.gear4music.com/Guitar-and-Bass/Boss-RC-5/3LJT", "product"),
        ("gear4music.com", "https://www.gear4music.com/Guitar_Effects/Looper/Boss", "category"),
        ("gear4music.com", "https://www.gear4music.com/blog/boss-loop-stations-explained", "content"),
        ("andertons.co.uk", "https://www.andertons.co.uk/boss-rc-5-loop-station-pedal", "product"),
        ("andertons.co.uk", "https://www.andertons.co.uk/browse/guitar-pedals/looper-pedals", "category"),
        ("andertons.co.uk", "https://www.andertons.co.uk/guitar-looper-pedal-guide", "content"),
        ("blog.andertons.co.uk", "https://blog.andertons.co.uk/learn/x", "content"),
        ("projectmusic.co.uk", "https://www.projectmusic.co.uk/x", "unknown"),
    ],
)  # fmt: skip
def test_page_type(host, url, expected):
    assert page_type(host, url) == expected


def test_attribute_price_skips_non_product_amounts():
    andertons = ("Free Next Day Delivery on orders over £50 | £0.00 basket\n# Boss RC-5 Loop Station Pedal\n"
                 "£219.00 10+ In Stock\nFree delivery when spending over £199\n£170 off the DSM pedal")
    ev = attribute_price(andertons, "RC-5")
    assert ev.price == Decimal("219.00") and ev.availability == "In Stock" and "219.00" in ev.context
    assert ev.candidates == 1
    gg = "BOSS RC-5 Loop Station | guitarguitar RRP £246.00 Save £37.00 £209.00 In Stock"
    assert attribute_price(gg, "RC-5").price == Decimal("209.00")
    thomann = "Epiphone Les Paul Standard 60s Ebony LH ... £ 554 All prices incl. VAT In stock within 2-3 weeks"
    ev = attribute_price(thomann, "Les Paul Standard 60s")
    assert ev.price == Decimal("554.00") and ev.availability == "In stock within 2-3 weeks"
    unavailable = "# Epiphone Les Paul Standard 60s Left Handed, Ebony\nThis product is currently not available"
    ev = attribute_price(unavailable, "Les Paul Standard 60s")
    assert ev.price is None and ev.availability == "currently not available"
    assert attribute_price("no model here £10", "RC-5").price is None
    assert attribute_price(None, "RC-5").price is None


# --------------------------------------------------------------------------- pool + summary


def _r(url, identity=Identity.EXACT, ptype="product", retailer=True, rank=1, host="andertons.co.uk"):
    return ResultRow(rank, url, url, host, None, None, None, identity, ptype, retailer, True, [])


def test_build_pool_candidates_negative_controls_and_merge():
    searches = [
        SearchRecord("exa", "domain", "SB-001", "a", results=[
            _r("u1"), _r("u2"), _r("u3"), _r("cat", ptype="category"), _r("used", Identity.CONDITION_MISMATCH)]),
        SearchRecord("tavily", "domain", "SB-001", "a", results=[_r("u1"), _r("u4"), _r("x", Identity.NO_MATCH)]),
        SearchRecord("tavily", "global", "SB-001", None, results=[_r("u5", retailer=False)]),
    ]  # fmt: skip
    pool = build_pool(searches, per_provider=2)["SB-001"]
    assert {u: (e.found_by, e.role) for u, e in pool.items()} == {
        "u1": (["exa", "tavily"], "candidate"), "u2": (["exa"], "candidate"),
        "used": (["exa"], "negative_control"), "u4": (["tavily"], "candidate"),
    }  # fmt: skip


def test_summarise_separates_stages_and_scores_identity(catalogue):
    products = [catalogue.products["SB-001"]]
    retailers = [c for c in catalogue.competitors if c.domain in ("andertons.co.uk", "gear4music.com")]
    searches = [
        SearchRecord("exa", "global", "SB-001", None, latency_ms=100, cost_usd="0.007",
                     results=[_r("g1", rank=2)]),
        SearchRecord("exa", "domain", "SB-001", "andertons.co.uk", latency_ms=200, cost_usd="0.007",
                     results=[_r("a1")]),
        SearchRecord("exa", "domain", "SB-001", "gear4music.com", latency_ms=300, cost_usd="0.007",
                     results=[_r("g-used", Identity.CONDITION_MISMATCH, host="gear4music.com")]),
        SearchRecord("tavily", "domain", "SB-001", "andertons.co.uk", ok=False, error="ProviderRateLimitError: x"),
        SearchRecord("tavily", "domain", "SB-001", "gear4music.com", latency_ms=50, cost_usd="0.008",
                     cost_detail="1 credits", results=[_r("g2", host="gear4music.com")]),
    ]  # fmt: skip
    retrievals = [
        RetrievalRecord("exa", "SB-001", "a1", "andertons.co.uk", ["exa"], "EXACT", "candidate", "OK",
                        content_identity=Identity.EXACT, attributed_price="219.00", availability="In Stock"),
        RetrievalRecord("tavily", "SB-001", "a1", "andertons.co.uk", ["exa"], "EXACT", "candidate", "OK",
                        content_identity=Identity.EXACT, attributed_price="219.00"),
        RetrievalRecord("tavily", "SB-001", "g2", "gear4music.com", ["tavily"], "EXACT", "candidate", "FAILED"),
    ]  # fmt: skip
    batches = [{"provider": "exa", "ok": True, "latency_ms": 900.0, "cost_usd": "0.001", "cost_basis": "PROVIDER_REPORTED"},
               {"provider": "tavily", "ok": True, "latency_ms": 500.0, "cost_usd": "0.0032", "cost_basis": "ESTIMATED",
                "cost_detail": "0.4 credits"}]  # fmt: skip
    s = summarise(["exa", "tavily"], products, retailers, searches, retrievals, batches)

    exa, tav = s["providers"]["exa"], s["providers"]["tavily"]
    assert s["targeted_union_exact_pairs"] == 2
    assert exa["targeted"]["coverage_pct"] == 50.0 and exa["targeted"]["recall_vs_union_pct"] == 50.0
    assert exa["targeted"]["pairs_only_variant_or_condition_mismatch"] == 1
    assert exa["global"]["mean_rank_first_exact_known_retailer"] == 2
    assert exa["cost"]["search_usd"] == "0.021" and exa["cost"]["retrieval_usd"] == "0.001"
    assert exa["retrieval"]["candidate_with_attributed_price_pct"] == 100.0
    assert tav["requests"]["search_errors"] == {"ProviderRateLimitError": 1}
    assert tav["cost"]["credits_reported_search"] == "1" and tav["cost"]["credits_retrieval"] == "0.4"
    assert tav["retrieval"]["failures_by_host"] == {"gear4music.com": 1}
    assert s["price_agreement"] == {"pages_ok_for_all": 1, "same_attributed_price": 1, "different_or_missing": {}}


# --------------------------------------------------------------------------- fixes from manual inspection


@pytest.mark.parametrize(
    "spec_id, title, url, expected",
    [
        ("SB-049", None, "https://www.bax-shop.co.uk/power-supply/nux-nad15-2500-15v-2500ma-power-supply-for-ac-25-mighty-20-mkii-mighty-space", Identity.ACCESSORY),
        ("SB-075", None, "https://www.bax-shop.co.uk/pitch-shifter/digitech-whammy-dt-cables", Identity.ACCESSORY),
        ("SB-081", "Protection Racket Hard Case for Line 6 Helix LT", "https://x.co.uk/p", Identity.ACCESSORY),
        ("SB-047", "Blackstar St. James 50 6L6 Amp Head Flight Case", "https://x.co.uk/p", Identity.ACCESSORY),
        # bundled case is not an accessory listing
        ("SB-028", None, "https://www.bax-shop.co.uk/electric-guitars/epiphone-les-paul-standard-60s-figured-lh-iced-tea-left-handed-electric-guitar-with-case", Identity.EXACT),
        ("SB-081", "Line 6 Helix LT Multi-Effects Processor", "https://x.co.uk/p", Identity.EXACT),
        # curly-quote spelling used by Andertons
        ("SB-028", "Epiphone Inspired by Gibson Les Paul Standard ‘60s Figured Left Handed Iced Tea Burst", "https://x.co.uk/p", Identity.EXACT),
        ("SB-021", None, "https://www.gear4music.com/G4M/Cort-G250-SE-Vivid-Burgundy-De-segunda-mano/6TZ8", Identity.CONDITION_MISMATCH),
    ],
)  # fmt: skip
def test_accessory_quote_and_condition_rules(spec_id, title, url, expected):
    assert classify_identity(SAMPLE_BY_ID[spec_id], title, url) == expected


def test_price_skips_recommended_price_and_reads_not_in_stock():
    bax = "# JHS Pedals 3 Series Reverb Effect Pedal  Recommended price £163 -39%    £99 incl. 20% VAT  In stock"
    assert attribute_price(bax, "3 Series Reverb").price == Decimal("99.00")
    g4m = "# JHS Pedals 3 Series Reverb   £ 90.00 Price Includes VAT @ 20%    Not currently in stock, available to order"
    ev = attribute_price(g4m, "3 Series Reverb")
    assert ev.price == Decimal("90.00") and ev.availability == "Not currently in stock"


def test_price_window_ignores_markdown_link_urls():
    noise = "".join(f"![img](https://cdn.example.com/{'x' * 200}/{i}.jpg) " for i in range(30))
    md = f"# Boss RC-5 Loop Station Pedal\n{noise}\n[Boss RC-5](https://x/rc5)\n£219.00\n10+ In Stock"
    assert attribute_price(md, "RC-5").price == Decimal("219.00")


def test_price_after_gig_bag_sentence_and_loyalty_credit_ignored():
    andertons = ("# Epiphone Inspired by Gibson Les Paul Standard ‘60s Figured Left Handed Electric Guitar in Iced Tea Burst\n"
                 "Includes premium gig bag.\n\n£599.00\n\nOut of Stock\n\n#### Earn 599 Loyalty Points\n\n"
                 "You'll get £5.99 to spend on your next order.")  # fmt: skip
    ev = attribute_price(andertons, "Les Paul Standard 60s")
    assert ev.price == Decimal("599.00") and ev.candidates == 1 and ev.availability == "Out of Stock"


def test_price_keywords_need_word_boundaries():
    andertons = ("# JHS 3 Series Reverb Pedal\n## Streamlined JHS Tone on a budget\n£89.99\n1 In Stock\n"
                 "Earn one point for every £1 you spend.")  # fmt: skip
    ev = attribute_price(andertons, "3 Series Reverb")
    assert ev.price == Decimal("89.99") and ev.candidates == 1
