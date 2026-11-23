"""Stage 9.2 human-review workflow (offline): worksheet, worksheet validation, decision template, flags.
Nothing here creates a reviewer decision or marks a field verified by itself."""

from decimal import Decimal

from app.stage9.review_pack import (
    UNVERIFIED,
    check_worksheet,
    decisions_template,
    flags,
    worksheet,
)
from app.stage9.scoring import IdentityVerdict

ITEMS = {
    "retrieved": [{"run": "A", "product_id": "SB-001", "retailer": "andertons.co.uk",
                   "url": "https://www.andertons.co.uk/boss-rc-5-loop-station-pedal",
                   "extracted": {"title": "Boss RC-5 Loop Station Pedal", "brand": "Boss", "model": "RC-5", "sku": "RC5",
                                 "price": "219.00", "currency": "GBP", "price_text": "£219.00",
                                 "availability_text": "10+ In Stock", "availability_status": "IN_STOCK"}}],
    "not_retrieved": [{"run": "A", "product_id": "SB-001", "retailer": "bax-shop.co.uk", "page_status": "BLOCKED"}],
}  # fmt: skip


def test_the_worksheet_covers_retrieved_listings_only_and_starts_unverified():
    rows = worksheet(ITEMS)
    assert len(rows) == 1 and rows[0]["retailer"] == "andertons.co.uk"  # blocked pages are not in it
    assert all(v == UNVERIFIED for k, v in rows[0]["verified"].items() if k != "note")
    assert rows[0]["extracted"]["price"] == "219.00" and rows[0]["reviewer"] is None


def test_an_untouched_worksheet_verifies_nothing():
    per_run, problems = check_worksheet(worksheet(ITEMS))
    assert per_run == {} and problems == []


def test_a_filled_row_becomes_scoring_input():
    rows = worksheet(ITEMS)
    rows[0].update(reviewer="MA", page_checked_at="2026-11-24T10:00:00Z", evidence={"quote": "£219.00", "ref": None})
    rows[0]["verified"].update(price="219.00", currency="GBP", identity="EXACT", availability="IN_STOCK")
    per_run, problems = check_worksheet(rows)
    assert problems == []
    v = per_run["A"].candidates[0]
    assert v.price == Decimal("219.00") and v.identity is IdentityVerdict.EXACT and v.availability == "IN_STOCK"
    assert v.title is None and v.model is None  # still unverified: left out of the scores


def test_verified_fields_need_a_reviewer_and_time_and_valid_values():
    rows = worksheet(ITEMS)
    rows[0]["verified"]["price"] = "219.00"
    _, problems = check_worksheet(rows)
    assert problems and "reviewer" in problems[0]
    rows[0].update(reviewer="MA", page_checked_at="2026-11-24T10:00:00Z")
    _, problems = check_worksheet(rows)
    assert problems and "evidence" in problems[0]  # evidence is required too
    rows[0]["evidence"] = {"quote": None, "ref": "screenshot-001.png"}
    rows[0]["verified"]["identity"] = "PROBABLY"
    _, problems = check_worksheet(rows)
    assert problems and "identity must be one of" in problems[0]
    rows[0]["verified"].update(identity=UNVERIFIED, price="two hundred")
    _, problems = check_worksheet(rows)
    assert problems and "not a number" in problems[0]


def test_the_decision_template_decides_nothing():
    pack = [{"domain": "djsos.co.uk", "company_name": "DJ SOS", "flags": ["SPECIALISM_HINT: x"],
             "llm1": {"company_type": "retailer"}}]  # fmt: skip
    (row,) = decisions_template(pack)
    assert row["decision"] == "UNDECIDED" and row["benchmark_eligible"] is False and row["reviewer"] is None


def test_review_flags():
    base = {"classification_status": "COMPLETED", "competitor_class": "DIRECT_COMPETITOR", "company_type": "retailer"}
    assert flags({**base, "domain": "shop.co.uk", "company_name": "Shop"}, "yes") == []
    assert "MARKETPLACE" in flags({**base, "domain": "ebay.co.uk", "company_type": "marketplace"}, "yes")
    assert any(f.startswith("SPECIALISM_HINT") for f in flags({**base, "domain": "dyproaudio.co.uk",
               "company_name": "DY Pro Audio", "rationale": "a DJ, audio and lighting shop"}, "yes"))  # fmt: skip
    assert any(f.startswith("DOMAIN_PARSE_DEFECT") for f in flags({**base, "domain": "com.ua"}, "no"))
    assert "UK_SERVING_NOT_CONFIRMED" in flags({**base, "domain": "musix.com"}, "unknown")


# --------------------------------------------------------------------------- proposed decisions


def pack_row(domain, flags_=(), ctype="retailer", products=("SB-001",)):
    return {"domain": domain, "company_name": domain.split(".")[0], "flags": list(flags_), "evidence_urls": [],
            "products_in_evidence": list(products), "llm1": {"company_type": ctype}}  # fmt: skip


def test_proposals_follow_the_review_rules():
    from app.stage9.review_pack import propose_decision

    approve = propose_decision(pack_row("goodshop.co.uk"))
    assert approve["decision"] == "APPROVE" and approve["company_type"] == "retailer"
    assert approve["benchmark_eligible"] is False and any("UK customers" in c for c in approve["checks"])
    assert propose_decision(pack_row("ebay.co.uk", ["MARKETPLACE"], "marketplace"))["decision"] == "DEFER"
    assert propose_decision(pack_row("sound-dynamics.co.uk"))["decision"] == "DEFER"  # named in the review
    assert propose_decision(pack_row("brand.com", [], "manufacturer"))["decision"] == "DEFER"
    rej = propose_decision(pack_row("com.ua", ["DOMAIN_PARSE_DEFECT (merged)"], "other"))
    assert rej["decision"] == "REJECT" and "four" in rej["reason"]


def test_the_decision_check_writes_nothing_and_enforces_the_rules():
    from app.stage9.review_pack import check_decisions

    rows = [
        {"domain": "goodshop.co.uk", "decision": "APPROVE", "company_type": "retailer", "benchmark_eligible": False},
        {"domain": "later.co.uk", "decision": "DEFER"},
        {"domain": "untouched.co.uk", "decision": "UNDECIDED"},
        {"domain": "com.ua", "decision": "REJECT", "company_type": None},
    ]
    would, problems = check_decisions(rows)
    assert problems == [] and [a.domain for a in would] == ["goodshop.co.uk", "com.ua"]
    assert all(a.benchmark_eligible is False for a in would)
    bad = [{"domain": "com.ua", "decision": "APPROVE", "company_type": "retailer"},
           {"domain": "x.co.uk", "decision": "APPROVE", "company_type": None},
           {"domain": "m.co.uk", "decision": "APPROVE", "company_type": "marketplace", "benchmark_eligible": True},
           {"domain": "y.co.uk", "decision": "MAYBE"}]  # fmt: skip
    would, problems = check_decisions(bad)
    assert would == [] and len(problems) == 4


# --------------------------------------------------------------------------- spreadsheet worksheet


def test_the_spreadsheet_round_trip_requires_evidence(tmp_path):
    import csv

    from app.stage9.review_pack import CSV_COLUMNS, csv_to_rows, worksheet_csv_rows

    path = tmp_path / "w.csv"
    rows = worksheet_csv_rows({**ITEMS, "retrieved": [{**ITEMS["retrieved"][0], "quotes": {"price": {"quote": "£219.00"}}}]})
    assert rows[0]["extracted_price_quote"] == "£219.00"
    rows[0].update(verified_price="219.00", verified_identity="EXACT", reviewer="MA", page_checked_at="2026-11-24T10:00Z")
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    _, problems = check_worksheet(csv_to_rows(path))
    assert problems and "evidence" in problems[0]  # no quote or reference recorded
    rows[0]["evidence_quote"] = "£219.00 inc. VAT"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    per_run, problems = check_worksheet(csv_to_rows(path))
    assert problems == [] and per_run["A"].candidates[0].price == Decimal("219.00")
    assert per_run["A"].candidates[0].model is None  # left UNVERIFIED: not scored


def test_apply_decisions_writes_only_to_the_isolated_database_after_validation(tmp_path):
    import json as _json

    from app.services.review_store import ReviewStore
    from app.stage9 import isolated_db
    from app.stage9.review_pack import main

    root = tmp_path / "stage9"
    (root / "review").mkdir(parents=True)
    isolated_db.create_empty(root / "review.sqlite3", protected=())
    rows = [{"domain": "goodshop.co.uk", "decision": "APPROVE", "company_type": "retailer", "benchmark_eligible": False},
            {"domain": "badshop.co.uk", "decision": "REJECT", "company_type": None, "benchmark_eligible": False},
            {"domain": "later.co.uk", "decision": "DEFER", "company_type": None, "benchmark_eligible": False}]  # fmt: skip
    (root / "review" / "domain-decisions.proposed.json").write_text(_json.dumps(rows), encoding="utf-8")
    assert main(["apply-decisions", "--dir", str(root), "--reviewer", "tester"]) == 0
    got = {d.domain: d for d in ReviewStore(root / "review.sqlite3").list_decisions()}
    assert set(got) == {"goodshop.co.uk", "badshop.co.uk"} and got["goodshop.co.uk"].benchmark_eligible is False
    assert got["goodshop.co.uk"].reviewer == "tester" and list((root / "backups").iterdir())
    # Re-applying is idempotent; an invalid file writes nothing.
    assert main(["apply-decisions", "--dir", str(root), "--reviewer", "tester"]) == 0
    rows.append({"domain": "x.co.uk", "decision": "APPROVE", "company_type": None})
    (root / "review" / "domain-decisions.proposed.json").write_text(_json.dumps(rows), encoding="utf-8")
    assert main(["apply-decisions", "--dir", str(root), "--reviewer", "tester"]) == 2
    assert len(ReviewStore(root / "review.sqlite3").list_decisions()) == 2
