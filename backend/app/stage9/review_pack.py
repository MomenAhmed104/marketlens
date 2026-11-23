"""Stage 9.2 human-review material, built offline from Run A/B artifacts and the evaluation DB.

    python -m app.stage9.review_pack build --dir ../data/stage9          # write the review files
    python -m app.stage9.review_pack check-worksheet --dir ../data/stage9  # validate a filled worksheet

`build` writes (new files only; nothing is approved, rejected or edited):
  DIR/review/domain-review-pack.json|.md        every discovered candidate, flags for the reviewer
  DIR/review/domain-decisions.template.json     one UNDECIDED row per domain, for the reviewer to fill
  DIR/review/extraction-verification.json|.md   every listing to verify, retrieved vs blocked/failed
  DIR/review/verification-worksheet.json        retrieved listings only, every field UNVERIFIED
  DIR/review/verification-worksheet.csv         the same rows as a spreadsheet, with evidence columns
  DIR/review/domain-decisions.proposed.json|.md proposed APPROVE / REJECT / DEFER per domain (never applied)
`decisions-check` validates the proposed decision file and prints what WOULD be written (dry run).
`apply-decisions` (only when authorised) validates again, backs up DIR/review.sqlite3, then writes the
APPROVE / REJECT rows to that isolated evaluation database (never data/review/); DEFER rows write nothing.
`check-worksheet` validates the filled worksheet (CSV if present, else JSON) and writes DIR/review/verifications.run<X>.json
(scoring input, `scoring.Verifications`); fields left UNVERIFIED stay out of the scores. It never
touches an existing worksheet, and `build` never overwrites a worksheet that already exists.

The evaluation database is opened read-only. UK-serving (LLM 1's claim, unverified) is kept apart
from physical UK location, which is not assessed here (a TLD is only a hint). Every extracted
field is marked UNVERIFIED until a human checks it against the live page.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

from app.services.domains import is_public_suffix

MARKETPLACE_HINTS = ("amazon.", "ebay.", "etsy.", "reverb.", "onbuy.", "fruugo.")
# A hint for the reviewer only (never a decision): DJ / pro-audio / lighting shops may sell an
# accessory without being guitar retailers.
DJ_PRO_AUDIO_WORDS = ("dj ", "dj,", " dj", "pro audio", "pro-audio", "lighting", "audio and lighting")
UNVERIFIED = "UNVERIFIED (pending human check)"


def _runs(root: Path) -> dict[str, Path]:
    out = {}
    for p in sorted((root / "runs").iterdir()):
        for name in ("A", "B"):
            if f"-run{name}-" in p.name:
                out[name] = p
    return out


def _load(folder: Path, name: str):
    return json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))


def _stored_candidates(db: Path) -> dict[str, dict]:
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT domain, record FROM review_candidates ORDER BY domain").fetchall()
        decisions = {d for (d,) in conn.execute("SELECT domain FROM domain_decisions")}
    finally:
        conn.close()
    out = {}
    for domain, rec in rows:
        r = json.loads(rec)
        r["_has_decision"] = domain in decisions
        out[domain] = r
    return out


def flags(c: dict, uk: str) -> list[str]:
    f = []
    ctype = c.get("company_type")
    if ctype == "marketplace" or c["domain"].startswith(MARKETPLACE_HINTS):
        f.append("MARKETPLACE")
    if ctype == "manufacturer":
        f.append("MANUFACTURER")
    if ctype in (None, "other", "directory") or c.get("competitor_class") == "NEEDS_REVIEW" \
            or c.get("classification_status") != "COMPLETED":  # fmt: skip
        f.append("AMBIGUOUS_BUSINESS")
    if uk != "yes":
        f.append("UK_SERVING_NOT_CONFIRMED" if uk == "unknown" else "NOT_UK_SERVING_PER_LLM1")
    if c.get("sells_new_musical_instruments") is False:
        f.append("USED_ONLY_PER_LLM1")
    text = f" {(c.get('company_name') or '').lower()} {(c.get('rationale') or '').lower()} {c['domain']} "
    if any(w in text for w in DJ_PRO_AUDIO_WORDS) or c["domain"].startswith("dj"):
        f.append("SPECIALISM_HINT: DJ / pro-audio shop (check relevance to guitar retail)")
    if is_public_suffix(c["domain"]):
        f.append("DOMAIN_PARSE_DEFECT (several sites merged under a public suffix; record predates the fix)")
    return f


def domain_pack(root: Path) -> list[dict]:
    runs = _runs(root)
    found_in: dict[str, list[str]] = {}
    for name, folder in runs.items():
        for c in ((_load(folder, "result").get("discovery") or {}).get("candidates") or []):
            found_in.setdefault(c["domain"], []).append(name)
    stored = _stored_candidates(root / "review.sqlite3")
    pack = []
    for domain, rec in stored.items():
        c = rec["candidate"]
        uk = c.get("serves_uk_customers") or "unknown"
        evidence = c.get("evidence") or []
        tld = domain.rsplit(".", 1)[-1]
        pack.append({
            "domain": domain,
            "company_name": c.get("company_name"),
            "hostnames": c.get("hostnames"),
            "discovered_in_runs": found_in.get(domain, []),
            "duplicate_across_runs": len(found_in.get(domain, [])) > 1,
            "times_seen": rec.get("times_seen"),
            "products_matched": c.get("products_matched"),
            "products_in_evidence": sorted({e["product_id"] for e in evidence if e.get("product_id")}),
            "discovery_queries": sorted({e["query_text"] for e in evidence if e.get("query_text")})[:6],
            "evidence_urls": sorted({e["url"] for e in evidence})[:8],
            "llm1": {
                "classification_status": c.get("classification_status"), "company_type": c.get("company_type"),
                "competitor_class": c.get("competitor_class"), "rationale": c.get("rationale"),
                "cited_evidence_urls": c.get("cited_evidence_urls"), "relevance_info_only": c.get("relevance"),
                "confidence_info_only": c.get("confidence"), "sells_new_musical_instruments": c.get("sells_new_musical_instruments"),
                "model": c.get("classification_model"),
            },
            "uk_serving_per_llm1_unverified": uk,
            "uk_serving_note": c.get("serves_uk_customers_note"),
            "physical_uk_location": f"not assessed (TLD .{tld} is only a hint)",
            "automatic_eligibility": c.get("approval_status"),
            "eligibility_reasons": c.get("decision_reasons"),
            "searched_in_stage9": False,
            "review_status": "DECIDED" if rec["_has_decision"] else "PENDING (no reviewer decision)",
            "flags": flags(c, uk),
        })  # fmt: skip
    return pack


def extraction_items(root: Path) -> dict[str, list[dict]]:
    retrieved, not_retrieved = [], []
    for name, folder in _runs(root).items():
        result = _load(folder, "result")
        for p in result.get("products") or []:
            intel = p.get("intelligence") or {}
            matches = {m["candidate_id"]: m for m in ((p.get("matching") or {}).get("candidates") or [])}
            for c in intel.get("candidates") or []:
                m = matches.get(c["candidate_id"], {})
                base = {"run": name, "product_id": p["product_id"], "retailer": c["competitor"]["domain"],
                        "url": c["product_url"], "page_status": c["page"]["status"],
                        "page_title": c["page"].get("title"), "retrieved_at": c["page"].get("retrieved_at"),
                        "match_status": m.get("match_status")}  # fmt: skip
                if c["page"]["status"] != "OK":
                    not_retrieved.append({**base, "error": c["page"].get("error"),
                                          "note": "Not retrieved: no extraction to verify; identity rests on the search result"})
                    continue
                quotes = {fe["field"]: {"status": fe["status"], "quote": fe.get("quote")} for fe in c.get("field_evidence") or []}
                bundle = next((v for v in m.get("variants") or [] if v["attribute"] == "bundle"), {})
                retrieved.append({**base,
                    "extraction_status": c["extraction_status"],
                    "extracted": {"title": c.get("product_title"), "brand": c.get("brand"), "model": c.get("model"),
                                  "sku": c.get("sku"), "price": c.get("price"), "currency": c.get("currency"),
                                  "price_text": c.get("price_text"), "availability_text": c.get("availability"),
                                  "availability_status": c.get("availability_status")},
                    "quotes": quotes,
                    "price_validation": (m.get("price") or {}).get("status"),
                    "price_compared": (m.get("comparison") or {}).get("eligible"),
                    "bundle_check": bundle.get("result"), "bundle_found": bundle.get("found"),
                    "ignored_prices": [i.get("text") for i in (c.get("ignored_price_details") or [])][:6],
                    "verification": {k: UNVERIFIED for k in ("title", "brand", "model", "price", "currency",
                                                             "price_is_current", "availability", "standalone_or_bundle")},
                })  # fmt: skip
    return {"retrieved": retrieved, "not_retrieved": not_retrieved}


DECISION_FIELDS = {
    "decision": "UNDECIDED",  # APPROVE | REJECT | DEFER (UNDECIDED = not reviewed)
    "company_type": None,  # retailer | specialist_retailer | manufacturer | marketplace | directory | other
    "benchmark_eligible": False,  # retailer types only; a separate choice from approval
    "reviewer": None,
    "notes": None,
}


def decisions_template(pack: list[dict]) -> list[dict]:
    """One row per candidate for the reviewer. Not applied anywhere: no decision is created."""
    return [{"domain": d["domain"], "company_name": d["company_name"], "flags": d["flags"],
             "llm1_company_type": d["llm1"]["company_type"], **DECISION_FIELDS} for d in pack]  # fmt: skip


VERIFY_FIELDS = ("title", "brand", "model", "price", "currency", "availability")
IDENTITY_VALUES = ("EXACT", "VARIANT_MISMATCH", "DIFFERENT_PRODUCT", "NOT_A_PRODUCT_PAGE", "UNKNOWN")
AVAILABILITY_VALUES = ("IN_STOCK", "OUT_OF_STOCK", "PREORDER", "BACKORDER", "DISCONTINUED", "UNKNOWN")


def worksheet(items: dict) -> list[dict]:
    """Retrieved listings only (blocked / failed pages have nothing to verify). Every field starts
    UNVERIFIED; the reviewer replaces a value only after checking the live page."""
    rows = []
    for i in items["retrieved"]:
        e = i["extracted"]
        rows.append({
            "run": i["run"], "product_id": i["product_id"], "retailer": i["retailer"], "url": i["url"],
            "extracted": {"title": e["title"], "brand": e["brand"], "model": e["model"], "price": e["price"],
                          "currency": e["currency"], "availability": e["availability_status"]},
            "page_checked_at": None, "reviewer": None, "evidence": {"quote": None, "ref": None},
            "verified": {**{f: UNVERIFIED for f in VERIFY_FIELDS}, "price_is_current": UNVERIFIED,
                         "identity": UNVERIFIED, "note": None},
        })  # fmt: skip
    return rows


def check_worksheet(rows: list[dict]) -> tuple[dict[str, "object"], list[str]]:
    """Validate a filled worksheet. Returns (run -> Verifications, problems). Fields still UNVERIFIED
    are left out; a row with any verified field needs `reviewer` and `page_checked_at`."""
    from decimal import Decimal, InvalidOperation

    from app.stage9.scoring import CandidateVerification, IdentityVerdict, Verifications

    out: dict[str, list] = {}
    problems = []
    for n, r in enumerate(rows, 1):
        v = r.get("verified") or {}
        set_fields = {k: val for k, val in v.items() if k != "note" and val not in (UNVERIFIED, None)}
        if not set_fields:
            continue
        where = f"row {n} ({r.get('run')} {r.get('product_id')} {r.get('retailer')})"
        if not r.get("reviewer") or not r.get("page_checked_at"):
            problems.append(f"{where}: verified fields need 'reviewer' and 'page_checked_at'")
            continue
        ev = r.get("evidence") or {}
        if not (ev.get("quote") or ev.get("ref")):
            problems.append(f"{where}: verified fields need evidence (a quote seen on the page or a reference)")
            continue
        kw = {"product_url": r["url"], "note": v.get("note")}
        if "identity" in set_fields:
            if set_fields["identity"] not in IDENTITY_VALUES:
                problems.append(f"{where}: identity must be one of {IDENTITY_VALUES}")
                continue
            kw["identity"] = IdentityVerdict(set_fields["identity"])
        if "availability" in set_fields and set_fields["availability"] not in AVAILABILITY_VALUES:
            problems.append(f"{where}: availability must be one of {AVAILABILITY_VALUES}")
            continue
        if "price" in set_fields:
            try:
                kw["price"] = Decimal(str(set_fields["price"]))
            except InvalidOperation:
                problems.append(f"{where}: price is not a number")
                continue
        for f in ("currency", "title", "model", "availability"):
            if f in set_fields:
                kw[f] = str(set_fields[f])
        out.setdefault(r["run"], []).append(CandidateVerification(**kw))
    return {run: Verifications(candidates=c) for run, c in out.items()}, problems


# --------------------------------------------------------------------------- proposed decisions

APPROVABLE_TYPES = ("retailer", "specialist_retailer", "manufacturer", "marketplace", "directory", "other")
UK_CHECK = ("Confirm on the retailer's own site that it sells to UK customers (delivery page or checkout): "
            "LLM 1's UK answer is not verified.")  # fmt: skip


# Candidates the 23 Nov 2026 review named for deferral although no automatic flag fires.
REVIEW_NOTED_DEFERRALS = {
    "sound-dynamics.co.uk": "Named as a DJ / pro-audio-leaning shop in the 23 Nov 2026 review (sells the Cherub tuner).",
    "musicgooddeal.com": "Named in the 23 Nov 2026 review: international seller; UK service rests only on LLM 1.",
}


def propose_decision(d: dict) -> dict:
    """A proposal for the reviewer, never applied. Search approval and benchmark eligibility stay
    separate: benchmark_eligible is always False here. Flagged candidates are deferred (no record);
    only the merged com.ua record is proposed for rejection."""
    flags_ = [f.split(":")[0].split(" ")[0] for f in d["flags"]]
    row = {"domain": d["domain"], "company_name": d["company_name"], "flags": d["flags"],
           "evidence_urls": d["evidence_urls"][:4], "products": d["products_in_evidence"],
           "benchmark_eligible": False, "reviewer": None, "status": "PROPOSED - not applied"}  # fmt: skip
    if "DOMAIN_PARSE_DEFECT" in flags_:
        return {**row, "decision": "REJECT", "company_type": None,
                "reason": "Merged record: four different .com.ua shops grouped under the public suffix 'com.ua' "
                          "(parsing defect, fixed). Reject the merged record; each shop needs its own validated "
                          "record after rediscovery.", "checks": []}  # fmt: skip
    if d["domain"] in REVIEW_NOTED_DEFERRALS:
        return {**row, "decision": "DEFER", "company_type": None, "reason": REVIEW_NOTED_DEFERRALS[d["domain"]],
                "checks": ["Decide relevance to guitar retail and UK service from the evidence pages"]}  # fmt: skip
    if flags_:
        return {**row, "decision": "DEFER", "company_type": None,
                "reason": "Flagged for human judgement: " + "; ".join(d["flags"]) + ". Stays undecided (no record).",
                "checks": ["Decide relevance and business type from the evidence pages"]}  # fmt: skip
    ctype = d["llm1"]["company_type"]
    if ctype not in ("retailer", "specialist_retailer"):
        return {**row, "decision": "DEFER", "company_type": None,
                "reason": f"LLM 1 type {ctype!r} is not a retailer type: needs a human decision.", "checks": []}
    return {**row, "decision": "APPROVE", "company_type": ctype,
            "reason": f"{ctype.replace('_', ' ')} with discovery evidence for {', '.join(d['products_in_evidence'])}; "
                      "no review flags. Search approval only, not a price benchmark.",
            "checks": [UK_CHECK, "Open one evidence page and confirm it is a shop selling new products"]}  # fmt: skip


def check_decisions(rows: list[dict], default_reviewer: str = "stage9-review") -> tuple[list, list[str]]:
    """Validate a decision file. Returns (records that WOULD be written, problems). DEFER and
    UNDECIDED rows produce no record. Nothing is written."""
    from app.schemas.competitor import CompanyType
    from app.schemas.review import ReviewDecision
    from app.stage9.isolated_db import Approval

    would, problems = [], []
    for r in rows:
        dec, dom = r.get("decision"), r.get("domain")
        if dec in ("DEFER", "UNDECIDED", None):
            continue
        if dec not in ("APPROVE", "REJECT"):
            problems.append(f"{dom}: decision must be APPROVE, REJECT, DEFER or UNDECIDED")
            continue
        if is_public_suffix(dom) and dec != "REJECT":
            problems.append(f"{dom}: a public-suffix (merged) record can only be rejected")
            continue
        ctype = r.get("company_type")
        if dec == "APPROVE" and ctype not in APPROVABLE_TYPES:
            problems.append(f"{dom}: an approval needs an explicit company_type {APPROVABLE_TYPES}")
            continue
        bench = r.get("benchmark_eligible") is True
        if bench and (dec != "APPROVE" or ctype not in ("retailer", "specialist_retailer", "manufacturer")):
            problems.append(f"{dom}: benchmark eligibility needs an approved retailer or manufacturer")
            continue
        would.append(Approval(domain=dom, decision=ReviewDecision.APPROVED if dec == "APPROVE" else ReviewDecision.REJECTED,
                              company_name=r.get("company_name"), company_type=CompanyType(ctype) if ctype else None,
                              benchmark_eligible=bench, reviewer=r.get("reviewer") or default_reviewer,
                              notes=r.get("reason")))  # fmt: skip
    return would, problems


def _md_decisions(rows: list[dict]) -> str:
    lines = ["# Stage 9.2 proposed domain decisions (NOT applied)", "",
             "Search approval only; benchmark eligibility is false for every row. DEFER = stays undecided.", "",
             "| Decision | Domain | Company | Type | Products | Reason | Checks before confirming |",
             "|---|---|---|---|---|---|---|"]  # fmt: skip
    order = {"REJECT": 0, "APPROVE": 1, "DEFER": 2}
    for r in sorted(rows, key=lambda x: (order.get(x["decision"], 3), x["domain"])):
        lines.append(f"| {r['decision']} | {r['domain']} | {r['company_name'] or '?'} | {r['company_type'] or '-'} | "
                     f"{', '.join(r['products'])} | {r['reason']} | {' '.join(r['checks']) or '-'} |")  # fmt: skip
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- spreadsheet worksheet

CSV_COLUMNS = ("row", "run", "product_id", "retailer", "url",
               "extracted_title", "extracted_brand", "extracted_model", "extracted_price", "extracted_currency",
               "extracted_availability", "extracted_price_quote",
               "verified_title", "verified_brand", "verified_model", "verified_price", "verified_currency",
               "verified_price_is_current", "verified_availability", "verified_identity",
               "evidence_quote", "evidence_ref", "reviewer", "page_checked_at", "note")  # fmt: skip


def worksheet_csv_rows(items: dict) -> list[dict]:
    rows = []
    for n, i in enumerate(items["retrieved"], 1):
        e = i["extracted"]
        rows.append({
            "row": n, "run": i["run"], "product_id": i["product_id"], "retailer": i["retailer"], "url": i["url"],
            "extracted_title": e["title"], "extracted_brand": e["brand"], "extracted_model": e["model"],
            "extracted_price": e["price"], "extracted_currency": e["currency"],
            "extracted_availability": e["availability_status"],
            "extracted_price_quote": ((i.get("quotes") or {}).get("price") or {}).get("quote"),
            **{f"verified_{f}": UNVERIFIED for f in ("title", "brand", "model", "price", "currency", "price_is_current",
                                                   "availability", "identity")},
            "evidence_quote": "", "evidence_ref": "", "reviewer": "", "page_checked_at": "", "note": "",
        })  # fmt: skip
    return rows


def csv_to_rows(path: Path) -> list[dict]:
    """Spreadsheet rows in the JSON worksheet shape (so one validator serves both)."""
    out = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            v = {k[len("verified_"):]: (r.get(k) or "").strip() or UNVERIFIED for k in r if k.startswith("verified_")}
            v["note"] = (r.get("note") or "").strip() or None
            out.append({"run": r["run"], "product_id": r["product_id"], "retailer": r["retailer"], "url": r["url"],
                        "reviewer": (r.get("reviewer") or "").strip() or None,
                        "page_checked_at": (r.get("page_checked_at") or "").strip() or None,
                        "evidence": {"quote": (r.get("evidence_quote") or "").strip() or None,
                                     "ref": (r.get("evidence_ref") or "").strip() or None},
                        "verified": v})  # fmt: skip
    return out


def apply_decisions_cmd(root: Path, proposed: Path, reviewer: str) -> int:
    """Validate, back up and apply the decision file to ROOT/review.sqlite3 (isolated only)."""
    from datetime import UTC, datetime

    from app.stage9.isolated_db import apply_decisions, check_destination, create_copy

    target = root / "review.sqlite3"
    try:
        check_destination(target)  # never the real or demo databases (data/review/)
    except Exception as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    if not target.is_file():
        print(f"refused: {target} does not exist", file=sys.stderr)
        return 2
    would, problems = check_decisions(json.loads(proposed.read_text(encoding="utf-8")), default_reviewer=reviewer)
    if problems:
        for p in problems:
            print(f"problem: {p}", file=sys.stderr)
        print("refused: the decision file has problems; nothing written", file=sys.stderr)
        return 2
    backup = root / "backups" / f"review.before-decisions.{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.sqlite3"
    create_copy(target, backup)
    written = apply_decisions(target, would)
    print(f"backup: {backup}")
    for d in written:
        print(f"WROTE: {d.decision.value} {d.domain} type={d.company_type and d.company_type.value} "
              f"benchmark={d.benchmark_eligible} reviewer={d.reviewer}")  # fmt: skip
    print(f"{len(written)} decision(s) applied to {target}")
    return 0


def _md_domains(pack: list[dict]) -> str:
    lines = ["# Stage 9.2 domain review pack", "",
             f"{len(pack)} candidates in the evaluation database; none has a reviewer decision; none was searched.",
             "UK-serving is LLM 1's unverified claim; physical UK location is not assessed.", "",
             "| Domain | Company | Runs | Type / class | Auto-eligible | UK-serving (LLM 1) | Products | Flags |",
             "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for d in sorted(pack, key=lambda x: (not x["flags"], x["domain"])):
        lines.append(f"| {d['domain']} | {d['company_name'] or '?'} | {','.join(d['discovered_in_runs']) or '-'}"
                     f"{' (dup)' if d['duplicate_across_runs'] else ''} | {d['llm1']['company_type'] or '?'} / "
                     f"{d['llm1']['competitor_class'] or '?'} | {d['automatic_eligibility']} | "
                     f"{d['uk_serving_per_llm1_unverified']} | {', '.join(d['products_in_evidence'])} | "
                     f"{'; '.join(d['flags']) or '-'} |")  # fmt: skip
    return "\n".join(lines) + "\n"


def _md_extraction(items: dict) -> str:
    lines = ["# Stage 9.2 extraction verification worksheet", "",
             "Every value below is UNVERIFIED until checked against the live page (docs/stage9/human-verification.md).", "",
             f"## Retrieved listings ({len(items['retrieved'])})", "",
             "| Run | Product | Retailer | Match | Title | Model | Price | Avail | Bundle | URL |", "|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for i in items["retrieved"]:
        e = i["extracted"]
        lines.append(f"| {i['run']} | {i['product_id']} | {i['retailer']} | {i['match_status']} | {(e['title'] or '')[:60]} | "
                     f"{e['model'] or ''} | {e['price'] or ''} {e['currency'] or ''} ({i['price_validation']}) | "
                     f"{e['availability_status'] or ''} | {i['bundle_check'] or ''} | {i['url']} |")  # fmt: skip
    lines += ["", f"## Blocked or failed pages ({len(items['not_retrieved'])}): nothing extracted", "",
              "| Run | Product | Retailer | Status | Error | URL |", "|---|---|---|---|---|---|"]
    for i in items["not_retrieved"]:
        lines.append(f"| {i['run']} | {i['product_id']} | {i['retailer']} | {i['page_status']} | {i['error'] or ''} | {i['url']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stage 9.2 human-review material (offline)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "check-worksheet", "decisions-check", "apply-decisions"):
        sp = sub.add_parser(name)
        sp.add_argument("--dir", type=Path, required=True)
        if name == "apply-decisions":
            sp.add_argument("--reviewer", required=True, help="who decided (recorded on every decision)")
    args = ap.parse_args(argv)
    root = args.dir
    out = root / "review"
    out.mkdir(parents=True, exist_ok=True)
    sheet = out / "verification-worksheet.json"
    sheet_csv = out / "verification-worksheet.csv"
    proposed = out / "domain-decisions.proposed.json"
    if args.cmd == "decisions-check":
        would, problems = check_decisions(json.loads(proposed.read_text(encoding="utf-8")))
        for p in problems:
            print(f"problem: {p}", file=sys.stderr)
        for a in would:
            print(f"WOULD write: {a.decision.value} {a.domain} type={a.company_type and a.company_type.value} "
                  f"benchmark={a.benchmark_eligible}")  # fmt: skip
        print(f"{len(would)} record(s) would be written (dry run: nothing written); {len(problems)} problem(s)")
        return 2 if problems else 0
    if args.cmd == "apply-decisions":
        return apply_decisions_cmd(root, proposed, args.reviewer)
    if args.cmd == "check-worksheet":
        rows = csv_to_rows(sheet_csv) if sheet_csv.exists() else json.loads(sheet.read_text(encoding="utf-8"))
        per_run, problems = check_worksheet(rows)
        for p in problems:
            print(f"problem: {p}", file=sys.stderr)
        for run, ver in per_run.items():
            (out / f"verifications.run{run}.json").write_text(ver.model_dump_json(indent=1), encoding="utf-8")
        print(f"{sum(len(v.candidates) for v in per_run.values())} verified listing(s) in {sorted(per_run)}; "
              f"{len(rows) - sum(len(v.candidates) for v in per_run.values())} still unverified; {len(problems)} problem(s)")
        return 2 if problems else 0
    pack = domain_pack(root)
    items = extraction_items(root)
    (out / "domain-review-pack.json").write_text(json.dumps(pack, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "domain-review-pack.md").write_text(_md_domains(pack), encoding="utf-8")
    decisions = out / "domain-decisions.template.json"
    if not decisions.exists():  # never overwrite a reviewer's work in progress
        decisions.write_text(json.dumps(decisions_template(pack), indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "extraction-verification.json").write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "extraction-verification.md").write_text(_md_extraction(items), encoding="utf-8")
    if not sheet.exists():
        sheet.write_text(json.dumps(worksheet(items), indent=1, ensure_ascii=False), encoding="utf-8")
    if not sheet_csv.exists():  # never overwrite a reviewer's spreadsheet
        with sheet_csv.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            w.writeheader()
            w.writerows(worksheet_csv_rows(items))
    if not proposed.exists():
        rows = [propose_decision(d) for d in pack]
        proposed.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
        (out / "domain-decisions.proposed.md").write_text(_md_decisions(rows), encoding="utf-8")
    print(f"{len(pack)} domains; {len(items['retrieved'])} retrieved listings and {len(items['not_retrieved'])} "
          f"blocked/failed pages -> {out}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
