"""Stage 6.3.6: the whole user workflow through the HTTP API, with mocked LLM 1, LLM 2 and search.

Run 1 discovers competitors (LLM 1, verified UK evidence), queues new domains as pending and does
not search them. A reviewer approves one domain (with benchmark permission) and rejects another
through the review API. Run 2 searches the approved domain, extracts with LLM 2 and matches in
Python. Checks the SSE stream, the run result the dashboard reads, availability (including a
geographic delivery restriction) and that the temporary review database is the only one used.
No network, no keys, no real database.
"""

import json

from fastapi.testclient import TestClient

from app.agents.intelligence.schemas import ExtractionStatus
from app.main import create_app
from app.schemas.common import AvailabilityStatus, DomainApprovalStatus, MatchStatus
from tests.discovery_fakes import FakeLLM
from tests.intelligence_fakes import FakeExtractLLM, FakeIntelSearch, FakePage, extraction
from tests.pipeline_fakes import harness
from tests.search_fakes import make_settings
from tests.test_discovery_agent import CLASSIFICATIONS, PLAN
from tests.test_intelligence_agent import AND, BECK, EXTRACTIONS, G4M, HITS, PAGES, PROPOSALS
from tests.test_runs_api import parse_sse, wait_terminal

GEO = "This product is currently unavailable for delivery to your country"
PAGES_2 = {**PAGES,
           G4M: FakePage(f"BOSS RC-5 Loop Station\n{GEO}", "BOSS RC-5"),
           BECK: FakePage("Boss RC-5 Loop Station\nModel: RC-5\n£185.00\nIn stock - Add to basket", "Boss RC-5")}  # fmt: skip
EXTRACTIONS_2 = {
    **EXTRACTIONS,
    # LLM 2 wrongly proposes OUT_OF_STOCK for a delivery restriction: Python must say UNKNOWN.
    G4M: extraction(product_title="BOSS RC-5 Loop Station", title_quote="BOSS RC-5 Loop Station",
                    availability=GEO, availability_quote=GEO, availability_normalized=AvailabilityStatus.OUT_OF_STOCK),
    BECK: extraction(product_title="Boss RC-5 Loop Station", title_quote="Boss RC-5 Loop Station", brand="Boss",
                     brand_quote="Boss RC-5", model="RC-5", model_quote="Model: RC-5", displayed_price="£185.00",
                     currency="GBP", price_quote="£185.00", availability="In stock", availability_quote="In stock"),
}  # fmt: skip


def test_discover_review_approve_rerun_extract_match_and_stream(catalogue, tmp_path):
    h = harness(catalogue, tmp_path, llm1=lambda: FakeLLM(PLAN, CLASSIFICATIONS),
                llm2=lambda: FakeExtractLLM(EXTRACTIONS_2, PROPOSALS),
                intel_search=lambda: FakeIntelSearch(HITS, PAGES_2),
                pipeline_search_auto_approved=False)  # fmt: skip
    app = create_app(make_settings(review_db_path=h.settings.review_db_path))
    with TestClient(app) as client:
        app.state.run_manager, app.state.review_store = h.manager, h.review  # one temporary database
        assert len(client.get("/api/catalogue/products").json()["items"]) == 100  # 1. catalogue

        # 2-3. Run 1: LLM 1 discovery with verified UK evidence; new domains are queued, not searched.
        run1 = client.post("/api/runs", json={"product_ids": ["SB-001"]}).json()["run_id"]
        with client.stream("GET", f"/api/runs/{run1}/stream") as resp:
            events1 = parse_sse(list(resp.iter_lines()))
        assert wait_terminal(client, run1)["status"] in ("COMPLETED", "PARTIALLY_COMPLETED")
        found = {e["data"]["payload"]["domain"]: e["data"]["payload"]
                 for e in events1 if e["event"] == "competitor_discovered"}  # fmt: skip
        beck_evt = found["beckettsmusic.co.uk"]
        assert (beck_evt["uk_assessed"], beck_evt["uk_evidence_sufficient"], beck_evt["uk_independent_sources"]) \
            == (True, True, 2)
        result1 = client.get(f"/api/runs/{run1}/result").json()
        searched1 = {t["domain"] for t in result1["search_targets"]}
        assert "beckettsmusic.co.uk" not in searched1 and "ebay.co.uk" not in searched1
        disc = {c["domain"]: c for c in result1["discovery"]["candidates"]}
        assert disc["beckettsmusic.co.uk"]["uk_review"]["verified_evidence"]  # quotes reach the dashboard
        assert disc["beckettsmusic.co.uk"]["price_benchmark"] is False  # automatic decisions never benchmark

        # 4. Domain review: pending with the UK evidence view; approve one, reject another.
        pending = {c["domain"]: c for c in client.get("/api/review/candidates").json()}
        assert pending["beckettsmusic.co.uk"]["review_status"] == "PENDING"
        assert pending["beckettsmusic.co.uk"]["uk_review"]["sufficient"] is True
        r = client.post("/api/review/candidates/beckettsmusic.co.uk/approve",
                        json={"reviewer": "Sam", "company_type": "specialist_retailer", "benchmark_eligible": True,
                              "notes": "UK shop with verified address"})  # fmt: skip
        assert r.status_code == 201 and r.json()["benchmark_eligible"] is True
        assert client.post("/api/review/candidates/ebay.co.uk/reject", json={"reviewer": "Sam"}).status_code == 201

        # 5-8. Run 2 searches the approved domain, extracts with LLM 2 and matches in Python.
        run2 = client.post("/api/runs", json={"product_ids": ["SB-001"]}).json()["run_id"]
        with client.stream("GET", f"/api/runs/{run2}/stream") as resp:
            events2 = parse_sse(list(resp.iter_lines()))
        final = wait_terminal(client, run2)
        types = [e["event"] for e in events2]
        assert types[0] == "run_started" and types[-1] in ("run_completed", "run_failed")
        assert {"search_started", "page_retrieved", "competitor_product_extracted", "match_validated"} <= set(types)
        result2 = client.get(f"/api/runs/{run2}/result").json()
        targets = {t["domain"]: t for t in result2["search_targets"]}
        assert targets["beckettsmusic.co.uk"]["approval_status"] == DomainApprovalStatus.APPROVED.value
        assert targets["beckettsmusic.co.uk"]["price_benchmark"] is True
        assert "ebay.co.uk" not in targets

        matches = {m["product_url"]: m for m in result2["products"][0]["matching"]["candidates"]}
        beck = matches[BECK]
        assert beck["extraction_status"] == ExtractionStatus.COMPLETED.value
        assert beck["match_status"] == MatchStatus.EXACT_MATCH.value and beck["comparison"]["eligible"] is True
        assert beck["comparison"]["difference_gbp"] == "-4.99"  # 185.00 - 189.99, competitor - Stompbox
        assert beck["availability"]["status"] == AvailabilityStatus.IN_STOCK.value
        g4m = matches[G4M]
        assert g4m["availability"]["status"] == AvailabilityStatus.UNKNOWN.value
        assert "geographic or delivery restriction" in g4m["availability"]["reason"]
        assert matches[AND]["match_status"] == MatchStatus.EXACT_MATCH.value

        # 9. Partial failure is reported, not hidden (the fake's Gibson page fails to retrieve).
        assert final["status"] in ("COMPLETED", "PARTIALLY_COMPLETED")
        assert final["summary"]["products_completed"] == 1
        dump = json.dumps([e["data"] for e in events1 + events2])
        assert "exa-test-key" not in dump
