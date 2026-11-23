"""Stage 9.2 matching changes (user-approved 23 Nov 2026), offline:

- similar matches need a compatible product type (RC-500 stays similar to RC-5; TR-2 tremolo,
  speaker cabinets and bass combos are not similar to a looper / amp head; "watt" is no evidence);
- "ITB" is an Iced Tea Burst alias; a verified Colour/Finish specification can confirm a finish;
- Thomann's "F" is NOT accepted as Figured (pending research).
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.agents.intelligence.schemas import NormalizedSpec, ProductIntelligence
from app.schemas.common import MatchStatus as M
from app.services.matching import attributes as A
from app.services.matching import evaluate_product, match_candidate
from app.services.matching.schemas import CheckResult
from tests.matching_fakes import NOW, candidate

RUN_A = Path(__file__).parent / "fixtures" / "stage92_runA.json"


def run(catalogue, pid, title, url="https://shop.example/x", model=None, specs=()):
    c = candidate(url, title, llm=True, product_title=title, model=model, price="100.00", quote="£100.00")
    c.specifications = [NormalizedSpec(name=n, value=v) for n, v in specs]
    return match_candidate(catalogue.products[pid], c, NOW, 24)


def check(m, attr):
    return next(v for v in m.variants if v.attribute == attr)


# --------------------------------------------------------------------------- product types


@pytest.mark.parametrize("text, kind", [
    ("Boss RC-5 Loop Station Guitar Effect Pedal", "looper"),
    ("Boss RC-500 Loop Station Pedal", "looper"),
    ("Boss TR-2 Tremolo", "tremolo"),
    ("Ashdown SX-50H All Valve Electric Guitar Amplifier Head, 50-Watts", "amp head"),
    ("Ashdown SX-212A Guitar Speaker Cabinet", "cabinet"),
    ("Blackstar HT-5R 1x12 Combo", "cabinet"),  # a speaker layout is read as a cabinet first
    ("Ashdown Studio 10 Bass Combo Amplifier", "combo amp"),
    ("Ashdown Capri Short Scale Bass", "bass guitar"),
    ("Cherub WST-905Li Clip-on Headstock Guitar Tuner", "tuner"),  # "Headstock" is not "head"
    ("Emma OMP-1 Booster Overdrive Pedal", "drive"),
    ("Ashdown 50 Watt", None),  # watt is never type evidence
])  # fmt: skip
def test_product_type(text, kind):
    assert A.product_type(text) == kind


def test_rc500_stays_similar_and_tr2_is_no_match(catalogue):
    assert run(catalogue, "SB-001", "Boss RC-500 Loop Station Pedal", model="RC-500").match_status is M.SIMILAR_MATCH
    m = run(catalogue, "SB-001", "Boss TR-2 Tremolo", model="TR-2")
    assert m.match_status is M.NO_MATCH and "tremolo vs looper" in m.match_reasons[0]


@pytest.mark.parametrize("title, model", [
    ("Ashdown SX-212A Guitar Speaker Cabinet", "SX-212A"),
    ("Ashdown Studio 10 Bass Combo Amplifier", "Studio 10"),
    ("Ashdown AGM-284C 15-Watt Combo Guitar Amplifier", "AGM-284C"),
    ("Ashdown CTM-30 50 Watt", "CTM-30"),  # no type stated: "watt" does not make it similar
])  # fmt: skip
def test_other_ashdown_products_are_not_similar_to_the_sx50h_head(catalogue, title, model):
    m = run(catalogue, "SB-017", title, model=model)
    assert m.match_status is M.NO_MATCH and not m.comparison.eligible


def test_another_ashdown_head_stays_similar(catalogue):
    assert run(catalogue, "SB-017", "Ashdown CTM-30 Valve Amplifier Head", model="CTM-30").match_status is M.SIMILAR_MATCH


def test_another_bass_of_the_same_brand_stays_similar(catalogue):
    m = run(catalogue, "SB-030", "Ashdown Capri Short Scale Roasted Series Bass", model="Capri")
    assert m.match_status is M.SIMILAR_MATCH


def test_untyped_catalogue_products_keep_the_previous_rule(catalogue):
    p = catalogue.products["SB-088"]  # "Noise gate": no type in the vocabulary
    assert A.profile(p).product_type is None
    title = f"{p.brand} XYZ-999 Pedal"
    m = run(catalogue, "SB-088", title, model="XYZ-999")
    assert m.match_status is M.SIMILAR_MATCH  # same brand, different model, as before


# --------------------------------------------------------------------------- finish


def test_itb_confirms_iced_tea_burst_but_f_is_not_figured(catalogue):
    m = run(catalogue, "SB-028", "Epiphone Les Paul Standard 60s F ITB LH",
            "https://www.thomann.co.uk/epiphone_les_paul_standard_60s_f_itb_lh.htm", model="Les Paul Standard 60s F ITB LH")
    assert check(m, "finish").result is CheckResult.MATCH
    assert check(m, "designation").result is CheckResult.UNVERIFIED  # "F" is not accepted as Figured
    assert m.match_status is M.REVIEW_REQUIRED and not m.comparison.eligible


@pytest.mark.parametrize("specs, result", [
    ([("Colour", "Iced Tea Burst high-gloss")], CheckResult.MATCH),
    ([("Finish", "Iced Tea Burst")], CheckResult.MATCH),
    ([("Body colour", "Iced Tea Burst satin")], CheckResult.MATCH),
    ([("Colour", "Iced Tea Burst / Ebony")], CheckResult.UNVERIFIED),  # mixed: never a match
    ([("Colour", "Ebony")], CheckResult.UNVERIFIED),  # a spec never creates a conflict
    ([("Top", "Iced Tea Burst")], CheckResult.UNVERIFIED),  # only Colour / Finish specifications count
    ([], CheckResult.UNVERIFIED),
])  # fmt: skip
def test_verified_colour_specification_can_confirm_the_finish(catalogue, specs, result):
    m = run(catalogue, "SB-028", "Epiphone Les Paul Standard 60s Figured Left-Handed Electric Guitar",
            "https://shop.example/epiphone-les-paul-standard-60s-figured-left-handed", model="Les Paul Standard 60s Figured",
            specs=specs)  # fmt: skip
    assert check(m, "finish").result is result


def test_a_title_conflict_is_not_overridden_by_a_specification(catalogue):
    m = run(catalogue, "SB-028", "Epiphone Les Paul Standard 60s Figured Left-Handed Ebony",
            model="Les Paul Standard 60s Figured", specs=[("Colour", "Iced Tea Burst")])  # fmt: skip
    assert check(m, "finish").result is CheckResult.CONFLICT


# --------------------------------------------------------------------------- Run A regression


def test_run_a_regression(catalogue):
    data = json.loads(RUN_A.read_text(encoding="utf-8"))
    now = datetime.fromisoformat(data["retrieved_at"])
    out = {}
    for intel in data["products"]:
        pi = ProductIntelligence.model_validate(intel)
        for c in evaluate_product(pi, catalogue.products[pi.product.product_id], 24, now).candidates:
            out[(pi.product.product_id, c.product_url.rstrip("/").split("/")[-1])] = c
    # Changed by the Stage 9.2 rules.
    assert out[("SB-001", "boss-tr-2-tremolo-used-no-box")].match_status is M.NO_MATCH
    for key in [k for k in out if k[0] == "SB-017"]:
        assert out[key].match_status is M.NO_MATCH, key  # cabinet, combos and "Studio 15"
    thomann = out[("SB-028", "epiphone_les_paul_standard_60s_f_itb_lh.htm")]
    assert check(thomann, "finish").result is CheckResult.MATCH and thomann.match_status is M.REVIEW_REQUIRED
    # Unchanged.
    assert out[("SB-001", "boss-rc-500-loop-station-pedal")].match_status is M.SIMILAR_MATCH
    exact = sorted(k[0] for k, c in out.items() if c.match_status is M.EXACT_MATCH)
    assert exact == ["SB-001"] * 4 + ["SB-028"] * 2
    assert sum(c.comparison.eligible for c in out.values()) == 5


# --------------------------------------------------------------------------- retailer names never establish type


@pytest.mark.parametrize("title, domain, company, expected", [
    ("Ashdown Studio 15 – Langley Guitar Centre", "langleyguitarcentre.co.uk", "Langley Guitar Centre", "Ashdown Studio 15"),
    ("Boss RC-5 Loop Station Guitar Loop Pedal at Gear4music", "gear4music.com", "Gear4music",
     "Boss RC-5 Loop Station Guitar Loop Pedal"),
    ("BOSS RC-5 Loop Station | guitarguitar", "guitarguitar.co.uk", "GuitarGuitar", "BOSS RC-5 Loop Station"),
    ("Boss RC-5 Loop Station – Thomann UK", "thomann.co.uk", "Thomann UK", "Boss RC-5 Loop Station"),
    ("Ashdown AGM-284C Combo | Bax Music", "bax-shop.co.uk", "Bax Music", "Ashdown AGM-284C Combo"),
])  # fmt: skip
def test_retailer_names_are_stripped_from_titles(title, domain, company, expected):
    from app.services.matching.matcher import _site_tokens, strip_site_names

    assert strip_site_names(title, _site_tokens(domain, company)) == expected


def test_a_retailer_name_never_establishes_the_product_type(catalogue):
    from tests.matching_fakes import target

    title = "Ashdown Studio 15 – Langley Guitar Centre"
    c = candidate("https://www.langleyguitarcentre.co.uk/collections/electric-guitars/products/ashdown-studio-15", title,
                  llm=True, product_title=None, model="Studio 15", price="479.00", quote="£479.00",
                  competitor=target("langleyguitarcentre.co.uk"))  # fmt: skip
    c.competitor = c.competitor.model_copy(update={"company_name": "Langley Guitar Centre"})
    m = match_candidate(catalogue.products["SB-017"], c, NOW, 24)
    # Neither "Guitar Centre" (retailer name) nor "electric-guitars" (site navigation) makes it a guitar.
    assert m.match_status is M.NO_MATCH and "not stated vs amp head" in m.match_reasons[0]
