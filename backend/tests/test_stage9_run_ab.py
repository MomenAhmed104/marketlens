"""Stage 9.2 preparation: the Runs A/B command and the scorer's bundle consistency.

Offline only: mocked Exa and OpenRouter transports, a temporary ledger and evaluation database,
fake keys, and a temporary copy of the approved answer key."""

import json
import shutil

import httpx
import pytest

from app.agents.discovery.agent import CompetitorDiscoveryAgent
from app.agents.intelligence import ProductIntelligenceAgent
from app.schemas.common import MatchStatus as M
from app.services.llm import build_llm2
from app.services.matching import attributes as A
from app.services.matching import match_candidate
from app.services.review_store import ReviewStore
from app.services.search import build_provider
from app.stage9 import run_ab, smoke
from app.stage9.budget import BudgetLedger
from app.stage9.evaluation import DEFAULT_ANSWER_KEY, load_answer_key
from app.stage9.isolated_db import Approval, apply_decisions
from app.stage9.scoring import IdentityVerdict, Verifications, rules_verdict
from tests.matching_fakes import NOW, candidate

PRICES = ["--exa-search-usd", "0.007", "--exa-page-usd", "0.001", "--llm-input-usd-per-token", "0.00000025",
          "--llm-output-usd-per-token", "0.000001"]
FAKE = {"exa_api_key": "exa-test-key", "openrouter_api_key": "or-test-key", "llm1_model": "test/m1",
        "llm2_model": "test/m2", "search_provider": "exa", "discovery_use_llm": True}
NEW_SHOP = "newshop-music.co.uk"


class Web:
    """Mock Exa: global searches return one new shop; domain searches return one product page."""

    def __init__(self):
        self.searches, self.contents, self.llm = [], [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if request.url.host == "api.exa.ai" and request.url.path == "/search":
            self.searches.append(body)
            dom = (body.get("includeDomains") or [NEW_SHOP])[0]
            return httpx.Response(200, json={"requestId": "s", "costDollars": {"total": 0.007}, "results": [
                {"url": f"https://www.{dom}/boss-rc-5-loop-station", "title": "Boss RC-5 Loop Station",
                 "highlights": ["Boss RC-5 Loop Station pedal"]}]})  # fmt: skip
        if request.url.host == "api.exa.ai":
            self.contents.append(body)
            urls = body["urls"]
            return httpx.Response(200, json={"requestId": "c", "costDollars": {"total": 0.001 * len(urls)},
                                             "statuses": [{"id": u, "status": "success"} for u in urls],
                                             "results": [{"url": u, "id": u, "title": "Boss RC-5 Loop Station",
                                                          "text": "Boss RC-5 Loop Station. £219.00. In stock. " * 20}
                                                         for u in urls]})  # fmt: skip
        self.llm.append(body)
        return httpx.Response(200, json={"id": "gen", "model": "m", "usage": {"prompt_tokens": 900,
                                         "completion_tokens": 50, "cost": 0.0002},
                                         "choices": [{"message": {"content": "not json"}, "finish_reason": "stop"}]})  # fmt: skip


def fake_providers(web, catalogue):
    def prepare(app):
        deps = app.state.run_manager.d
        s = deps.settings

        def client():
            return httpx.AsyncClient(transport=httpx.MockTransport(web))

        async def disc(decisions):  # real discovery agent on mocked Exa (LLM 1 not exercised here)
            return CompetitorDiscoveryAgent(build_provider("exa", s, client()), None, s, catalogue.competitors,
                                            [p.category for p in catalogue.products.values()],
                                            llm_disabled_reason="test: no LLM 1", review_decisions=decisions)  # fmt: skip

        async def intel(targets, skipped):
            return ProductIntelligenceAgent(build_provider("exa", s, client()), build_llm2(s, client()), s, targets,
                                            skipped)  # fmt: skip

        deps.discovery_factory, deps.intelligence_factory = disc, intel

    return prepare


@pytest.fixture
def stage9(tmp_path):
    assert smoke.main(["setup", "--dir", str(tmp_path / "stage9")]) == 0
    key = tmp_path / "answer-key.json"
    shutil.copy(DEFAULT_ANSWER_KEY, key)  # the approved key, copied so tests never touch the real one
    return tmp_path / "stage9", key


def args(cmd, run, d):
    return [cmd, "--run", run, "--dir", str(d), *PRICES]


def last_json(text):
    return json.loads(text[text.rindex("\n{") + 1:] if "\n{" in text else text)


# --------------------------------------------------------------------------- command


def test_check_reports_the_approved_scope_and_the_cap(stage9, capsys):
    d, key = stage9
    assert run_ab.main(args("check", "A", d), settings_overrides=FAKE, answer_key=key) == 0
    report = json.loads(capsys.readouterr().out.split("\nPreflight passed")[0])
    assert report["scope"]["product_ids"] == ["SB-001", "SB-028", "SB-017"] and report["scope"]["run_discovery"]
    assert len(report["scope"]["domains"]) == 13 and report["scope"]["automatic_search"] is False
    assert report["cap_usd"] == "0.75" and report["worst_case"]["reservation_usd"] == "0.75"
    assert run_ab.main(args("check", "B", d), settings_overrides=FAKE, answer_key=key) == 0


@pytest.mark.parametrize("change, fragment", [
    ({"llm1_model": None}, "LLM 1 is not configured"),
    ({"pipeline_search_auto_approved": True}, None),  # forced off by the command: never a problem
])  # fmt: skip
def test_settings_are_enforced(stage9, capsys, change, fragment):
    d, key = stage9
    code = run_ab.main(args("check", "A", d), settings_overrides={**FAKE, **change}, answer_key=key)
    out = capsys.readouterr().out
    if fragment:
        assert code == 2 and fragment in out
    else:
        assert code == 0


def test_reviewer_decisions_before_both_runs_block_the_run(stage9, capsys):
    d, key = stage9
    apply_decisions(d / "review.sqlite3", [Approval(domain="some-shop.co.uk")])
    assert run_ab.main(args("check", "A", d), settings_overrides=FAKE, answer_key=key) == 2
    assert "reviewer decision" in capsys.readouterr().out


def test_an_unapproved_answer_key_blocks_the_run(stage9, tmp_path):
    d, key = stage9
    raw = json.loads(key.read_text("utf-8"))
    raw["status"] = "DRAFT"
    key.write_text(json.dumps(raw), "utf-8")
    assert run_ab.main(args("check", "A", d), settings_overrides=FAKE, answer_key=key) == 2


def test_the_wrong_cap_or_ceiling_blocks_the_run(tmp_path):
    d = tmp_path / "s9"
    BudgetLedger.create(d / "ledger.sqlite3", __import__("decimal").Decimal("5.00"))
    smoke.create_empty(d / "review.sqlite3")
    key = tmp_path / "k.json"
    shutil.copy(DEFAULT_ANSWER_KEY, key)
    assert run_ab.main(args("check", "A", d), settings_overrides=FAKE, answer_key=key) == 2  # ceiling must be 3.00


def test_run_a_then_b_in_one_database_with_discovery_and_only_configured_retailers(stage9, catalogue, capsys):
    d, key = stage9
    web = Web()
    assert run_ab.main(args("run", "A", d), settings_overrides=FAKE, prepare_app=fake_providers(web, catalogue),
                       answer_key=key) == 0  # fmt: skip
    capsys.readouterr()
    # The discovered shop is pending in the evaluation database, with no decision, and was never searched.
    store = ReviewStore(d / "review.sqlite3")
    assert NEW_SHOP in {c.candidate.domain for c in store.list_candidates()} and store.list_decisions() == []
    domain_searches = [b["includeDomains"][0] for b in web.searches if b.get("includeDomains")]
    product_searches = [x for x in domain_searches if x != NEW_SHOP]
    assert set(product_searches) == set(run_ab.configured_domains(smoke.smoke_settings(smoke.Paths(d), {
        "exa_search": 1, "exa_page": 1, "llm_input": 1, "llm_output": 1}, **FAKE)))  # the 13 configured
    # Profile search of the new shop is discovery evidence only; no product search was made there.
    assert all(b.get("query") != "Boss RC-5 Loop Station Guitar Effect Pedal" for b in web.searches
               if b.get("includeDomains") == [NEW_SHOP])  # fmt: skip
    # Run B in the same, unreset database.
    assert run_ab.main(args("run", "B", d), settings_overrides=FAKE, prepare_app=fake_providers(web, catalogue),
                       answer_key=key) == 0  # fmt: skip
    capsys.readouterr()
    led = BudgetLedger(d / "ledger.sqlite3")
    s = led.summary()
    assert s.runs == {"RECONCILED": 2} and s.ceiling_usd == 3
    runs = sorted((d / "runs").iterdir())
    assert len(runs) == 2 and "-runA-" in runs[0].name and "-runB-" in runs[1].name
    for folder in runs:
        manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
        assert manifest["settings"]["stage9_max_reservation_per_run_usd"] == "0.75"
        assert manifest["settings"]["pipeline_search_auto_approved"] is False
        entry = led.run(manifest["run_id"])
        assert entry["reserved_usd"] == "0.75" and entry["status"] == "RECONCILED"
        t = Verifications.model_validate_json((folder / "verifications.template.json").read_text("utf-8"))
        assert all(c.identity is IdentityVerdict.UNKNOWN for c in t.candidates)  # nothing pre-judged
        assert set(t.domain_relevance.values()) <= {"UNKNOWN"}
    # Run A's RC-5 pages are in its template (Run B's products do not match the mock page).
    t_a = Verifications.model_validate_json((runs[0] / "verifications.template.json").read_text("utf-8"))
    assert t_a.candidates and NEW_SHOP in t_a.domain_relevance
    # Never repeated.
    assert run_ab.main(args("check", "A", d), settings_overrides=FAKE, answer_key=key) == 2


# --------------------------------------------------------------------------- scorer: same bundle semantics


@pytest.fixture(scope="module")
def key():
    return load_answer_key()


@pytest.mark.parametrize("pid, title, model, expected", [
    # Reported false failures: optional / required extras are the standalone product.
    ("SB-001", "Boss RC-5 Loop Station (power supply not included)", "RC-5", IdentityVerdict.EXACT),
    ("SB-001", "Boss RC-5 Loop Station - requires 9V power supply", "RC-5", IdentityVerdict.EXACT),
    # Reported missed bundles: never exact.
    ("SB-001", "Boss RC-5 Loop Station Starter Package", "RC-5", IdentityVerdict.VARIANT_MISMATCH),
    ("SB-090", "Cherub WST-905Li Flow Tune Clip-on Tuner with Battery", "WST-905Li", IdentityVerdict.VARIANT_MISMATCH),
    ("SB-017", "Ashdown SX-50H Valve Head with Footswitch", "SX-50H", IdentityVerdict.VARIANT_MISMATCH),
    ("SB-028", "Epiphone Les Paul Standard 60s Figured Left-Handed Iced Tea Burst with Hard Case",
     "Les Paul Standard 60s Figured", IdentityVerdict.AMBIGUOUS_BUNDLE),
    # Bundles and ambiguous accessory wording.
    ("SB-001", "Boss RC-5 Loop Station Guitar Loop Pedal with Power Supply", "RC-5", IdentityVerdict.VARIANT_MISMATCH),
    ("SB-001", "Boss RC-5 Loop Station Power Supply", "RC-5", IdentityVerdict.AMBIGUOUS_BUNDLE),
    ("SB-090", "Cherub WST-905Li Clip-on Tuner 2 Pack", "WST-905Li", IdentityVerdict.VARIANT_MISMATCH),
    # Standalone and other non-bundle rules unchanged.
    ("SB-001", "Boss RC-5 Loop Station Guitar Loop Pedal", "RC-5", IdentityVerdict.EXACT),
    ("SB-001", "Boss RC-5 Loop Station Nearly New", "RC-5", IdentityVerdict.VARIANT_MISMATCH),
    ("SB-017", "Ashdown SX-50H 2x12 Combo", "SX-50H", IdentityVerdict.VARIANT_MISMATCH),
    ("SB-030", "Ashdown Saint Artisan Bass", "Saint Artisan", IdentityVerdict.UNKNOWN),  # unknown stays unscored
])  # fmt: skip
def test_scorer_uses_the_matcher_bundle_semantics(key, catalogue, pid, title, model, expected):
    prof = A.profile(catalogue.products[pid])
    verdict, why = rules_verdict(key.entry(pid), f"https://shop.example/x {title} {model}", "UNKNOWN", prof)
    assert verdict is expected, (title, why)


@pytest.mark.parametrize("pid, title, model", [
    ("SB-001", "Boss RC-5 Loop Station (power supply not included)", "RC-5"),
    ("SB-001", "Boss RC-5 Loop Station - requires 9V power supply", "RC-5"),
    ("SB-001", "Boss RC-5 Loop Station Starter Package", "RC-5"),
    ("SB-090", "Cherub WST-905Li Flow Tune Clip-on Tuner with Battery", "WST-905Li"),
    ("SB-017", "Ashdown SX-50H Valve Head with Footswitch", "SX-50H"),
    ("SB-028", "Epiphone Les Paul Standard 60s Figured Left-Handed Iced Tea Burst with Hard Case",
     "Les Paul Standard 60s Figured"),
    ("SB-001", "Boss RC-5 Loop Station Power Supply", "RC-5"),
])  # fmt: skip
def test_scorer_and_matcher_agree_on_bundles(key, catalogue, pid, title, model):
    """The matcher's decision is never contradicted by the scorer's bundle verdict."""
    c = candidate("https://shop.example/x", title, llm=True, product_title=title, model=model, price="100.00",
                  quote="£100.00")  # fmt: skip
    m = match_candidate(catalogue.products[pid], c, NOW, 24)
    verdict, _ = rules_verdict(key.entry(pid), f"{c.product_url} {title} {model}", "UNKNOWN",
                               A.profile(catalogue.products[pid]))  # fmt: skip
    if verdict is IdentityVerdict.EXACT:
        assert m.match_status is not M.SIMILAR_MATCH or [v for v in m.variants if v.attribute == "bundle"
                                                         and v.result.value == "CONFLICT"] == []  # fmt: skip
    else:  # VARIANT_MISMATCH / AMBIGUOUS_BUNDLE: the matcher never calls it exact
        assert m.match_status is not M.EXACT_MATCH, (title, m.match_status)
