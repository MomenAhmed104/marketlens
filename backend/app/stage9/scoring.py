"""Stage 9 scoring: a run's results against the answer key and human verifications.

Every metric reports raw counts (numerator / denominator) plus a percentage, and what it left
out and why. Rules (from the Stage 9.0 brief):

  - a product that was never searched is NOT_SEARCHED, never NO_MATCH, and is left out of every
    match metric;
  - a genuine NO_MATCH is not a failure;
  - a page that was not retrieved is left out of extraction accuracy (not counted as wrong);
  - UNKNOWN answer-key values and unverified candidates are left out (never scored as wrong);
  - an EXACT_MATCH that is really a variant / condition / configuration mismatch is a failure;
  - a price comparison must pass every rule independently (benchmark competitor, exact match,
    valid current GBP price, fresh verified evidence, condition comparable to new, correct
    difference); products whose key says price_scoring_eligible=false (SB-028) are excluded.

Human verification (`Verifications`, filled in after a run from the source pages) supplies the
ground truth the key cannot: whether a discovered domain is relevant, and what a candidate page
really shows. Without it, those metrics report "not verified" instead of a score.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.services.matching import attributes as A
from app.stage9.evaluation import AnswerKey, ExpectedOutcome, Label, group_hit


class Relevance(StrEnum):
    RELEVANT = "RELEVANT"
    NOT_RELEVANT = "NOT_RELEVANT"
    UNKNOWN = "UNKNOWN"


class IdentityVerdict(StrEnum):
    EXACT = "EXACT"  # the page is the exact catalogue product (new)
    VARIANT_MISMATCH = "VARIANT_MISMATCH"  # same model, other variant / condition / configuration / bundle
    AMBIGUOUS_BUNDLE = "AMBIGUOUS_BUNDLE"  # an extra is named but not shown to be excluded: never the exact product
    DIFFERENT_PRODUCT = "DIFFERENT_PRODUCT"
    NOT_A_PRODUCT_PAGE = "NOT_A_PRODUCT_PAGE"
    UNKNOWN = "UNKNOWN"


class CandidateVerification(BaseModel):
    """What a human saw on the live source page (any field may be left out = not verified)."""

    product_url: str
    identity: IdentityVerdict = IdentityVerdict.UNKNOWN
    price: Decimal | None = None  # current displayed price on the page
    currency: str | None = None
    title: str | None = None
    model: str | None = None
    availability: str | None = None  # IN_STOCK, OUT_OF_STOCK, PREORDER, ...
    note: str | None = None


class Verifications(BaseModel):
    domain_relevance: dict[str, Relevance] = Field(default_factory=dict)
    candidates: list[CandidateVerification] = Field(default_factory=list)

    def candidate(self, url: str) -> CandidateVerification | None:
        return next((c for c in self.candidates if c.product_url == url), None)


class Metric(BaseModel):
    name: str
    numerator: int = 0
    denominator: int = 0
    excluded: dict[str, int] = Field(default_factory=dict)  # reason -> count, never in the denominator
    failures: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def pct(self) -> float | None:
        return round(100 * self.numerator / self.denominator, 1) if self.denominator else None

    def exclude(self, reason: str) -> None:
        self.excluded[reason] = self.excluded.get(reason, 0) + 1

    def dump(self) -> dict:
        return {**self.model_dump(), "pct": self.pct}


class Scorecard(BaseModel):
    run_id: str | None
    run_status: str | None
    product_states: dict[str, str]
    metrics: dict[str, dict]
    cost_latency: dict[str, Any]


# --------------------------------------------------------------------------- helpers


def _searched_domains(product: dict) -> set[str]:
    intel = product.get("intelligence") or {}
    return {d["domain"] for d in intel.get("domains") or [] if d.get("queries")}


def product_state(product: dict) -> str:
    """SEARCHED, or why the product's results cannot be scored as match outcomes."""
    if product.get("status") in ("PENDING", "RUNNING"):
        return "NOT_FINISHED"
    if not product.get("intelligence"):
        return "NOT_SEARCHED"
    if not _searched_domains(product):
        return "NOT_SEARCHED"
    if product.get("status") != "COMPLETED" or not product.get("matching"):
        return "SEARCH_INCOMPLETE"
    return "SEARCHED"


def _intel_candidates(product: dict) -> dict[str, dict]:
    intel = product.get("intelligence") or {}
    return {c["candidate_id"]: c for c in intel.get("candidates") or []}


def _text(match: dict, intel: dict | None) -> str:
    parts = [match.get("product_url") or ""]
    if intel:
        parts += [(intel.get("page") or {}).get("title") or "", (intel.get("search") or {}).get("title") or "",
                  intel.get("product_title") or "", intel.get("model") or "", intel.get("sku") or ""]  # fmt: skip
    return " ".join(parts)


# Answer-key groups about extras sold with the product. They are decided with the matcher's own
# bundle semantics (`attributes.bundle_evidence`), so "power supply not included" or "requires 9V
# power supply" is never a mismatch, and "with footswitch" or "starter package" is never exact.
BUNDLE_GROUPS = frozenset({"accessory", "bundle"})


def bundle_verdict(text: str, profile: A.ProductProfile | None) -> tuple[IdentityVerdict | None, list[str]]:
    """VARIANT_MISMATCH for a bundle, AMBIGUOUS_BUNDLE for unclear extras, None when standalone.
    Mirrors matcher._bundle_check for a standalone Stompbox product (all six scenario products)."""
    own = profile.own_extras if profile else frozenset()
    be = A.bundle_evidence(text, own)
    if profile is not None and profile.bundle:
        return None, []  # a Stompbox bundle: the matcher compares its extras; not judged here
    if be.kind == "BUNDLE":
        instrument = profile is not None and profile.strings_default is not None
        if instrument and be.included and be.included <= A.INSTRUMENT_STANDARD_EXTRAS and not A.is_bundle(text):
            return IdentityVerdict.AMBIGUOUS_BUNDLE, [f"listed with {', '.join(sorted(be.included))} (instrument)"]
        what = ", ".join(sorted(be.included)) or "bundle / pack wording"
        return IdentityVerdict.VARIANT_MISMATCH, [f"bundle: {what}"]
    if be.kind == "AMBIGUOUS":
        return IdentityVerdict.AMBIGUOUS_BUNDLE, [f"names {', '.join(sorted(be.mentioned))} without saying it is excluded"]
    return None, []


def rules_verdict(entry, text: str, condition: str | None,
                  profile: A.ProductProfile | None = None) -> tuple[IdentityVerdict, list[str]]:  # fmt: skip
    """The answer key's identity rules applied to a candidate's title/URL/extracted model text.

    VARIANT_MISMATCH when a (non-bundle) not_exact_if term, a used/refurbished condition or a bundle
    is present; AMBIGUOUS_BUNDLE when an extra is named without being excluded; EXACT only when every
    required group is satisfied; otherwise UNKNOWN (not scored). Bundle and accessory groups follow
    the matcher's bundle semantics (`bundle_verdict`)."""
    why = []
    for g in entry.not_exact_if:
        if g.name in BUNDLE_GROUPS:
            continue
        hit = group_hit(text, g)
        if hit:
            why.append(f"{g.name}: '{hit}'")
    if condition in ("USED", "REFURBISHED"):
        why.append(f"condition {condition}")
    bundle, bundle_why = bundle_verdict(text, profile)
    if bundle is IdentityVerdict.VARIANT_MISMATCH:
        why += bundle_why
    if why:
        return IdentityVerdict.VARIANT_MISMATCH, why
    if bundle is IdentityVerdict.AMBIGUOUS_BUNDLE:
        return bundle, bundle_why
    missing = [g.name for g in entry.exact_requires if not group_hit(text, g)]
    if missing:
        return IdentityVerdict.UNKNOWN, [f"not confirmed: {', '.join(missing)}"]
    return IdentityVerdict.EXACT, []


# --------------------------------------------------------------------------- metrics


def score_run(key: AnswerKey, run: dict | None, result: dict | None, *, ledger: dict | None = None,
              duration_s: float | None = None, verifications: Verifications | None = None,
              review_decisions: list[dict] | None = None, catalogue_prices: dict[str, Decimal] | None = None,
              catalogue=None) -> Scorecard:  # fmt: skip
    """Score one run (the JSON written by the harness: run.json, result.json, ledger.json).

    `catalogue` (optional) gives each product's matching profile, so bundle rules know instruments
    and accessory products exactly as the matcher does."""
    profiles = {pid: A.profile(p) for pid, p in catalogue.products.items()} if catalogue is not None else {}
    v = verifications or Verifications()
    result = result or {}
    products = {p["product_id"]: p for p in result.get("products") or []}
    states = {pid: product_state(p) for pid, p in products.items()}
    m = {name: Metric(name=name) for name in (
        "discovery_relevance", "domain_eligibility", "retrieval_vs_answer_key", "page_retrieval",
        "extraction_accuracy", "exact_match_precision", "similar_match_handling", "price_comparison_correctness",
        "run_completion")}  # fmt: skip

    # Discovery relevance: discovered domains a human labelled; unlabelled ones are left out.
    for c in ((result.get("discovery") or {}).get("candidates") or []):
        label = v.domain_relevance.get(c["domain"], Relevance.UNKNOWN)
        if label is Relevance.UNKNOWN:
            m["discovery_relevance"].exclude("not labelled by a human")
            continue
        m["discovery_relevance"].denominator += 1
        if label is Relevance.RELEVANT:
            m["discovery_relevance"].numerator += 1
        else:
            m["discovery_relevance"].failures.append(f"{c['domain']}: not relevant")

    # Domain eligibility: every searched domain was a target; nothing pending, rejected or a
    # directory was searched; automatic targets never benchmarks; reviewer rejections respected.
    d = m["domain_eligibility"]
    targets = {t["domain"]: t for t in result.get("search_targets") or []}
    outcomes = {o["domain"]: o["status"] for o in result.get("domain_outcomes") or []}
    rejected = {x["domain"] for x in (review_decisions or []) if x.get("decision") == "REJECTED"}
    for pid, p in products.items():
        for dom in sorted(_searched_domains(p)):
            d.denominator += 1
            t = targets.get(dom)
            problems = []
            if t is None:
                problems.append("not a search target")
            if outcomes.get(dom) in ("PENDING_APPROVAL", "REJECTED", "AUTO_ELIGIBLE_NOT_SEARCHED"):
                problems.append(f"outcome {outcomes[dom]}")
            if dom in rejected:
                problems.append("rejected by a reviewer")
            if t and t.get("company_type") == "directory":
                problems.append("directory")
            if t and t.get("approval_status") == "AUTO_APPROVED" and t.get("price_benchmark"):
                problems.append("automatic target marked as benchmark")
            if problems:
                d.failures.append(f"{pid} {dom}: {', '.join(problems)}")
            else:
                d.numerator += 1

    # Retrieval against the key: for EXACT_LISTED expectations, did the run find an EXACT page there?
    r = m["retrieval_vs_answer_key"]
    for e in key.entries:
        p = products.get(e.product_id)
        for exp in e.retailers:
            if exp.expected is not ExpectedOutcome.EXACT_LISTED or exp.label is Label.UNKNOWN:
                continue
            if p is None or states.get(e.product_id) != "SEARCHED":
                r.exclude("product not searched")
                continue
            if exp.domain not in _searched_domains(p):
                r.exclude("retailer not searched in this run")
                continue
            r.denominator += 1
            exact = [c for c in (p["matching"]["candidates"]) if c["competitor"]["domain"] == exp.domain
                     and c["match_status"] == "EXACT_MATCH"]  # fmt: skip
            if exact:
                r.numerator += 1
            else:
                r.failures.append(f"{e.product_id} {exp.domain}: no EXACT_MATCH ({exp.label.value} expectation)")
        r.notes = ["Expectations are hypotheses from historical records: a miss may mean the listing changed."]

    # Page retrieval: pages requested vs retrieved OK (provider-side success).
    usage = result.get("intelligence_usage") or {}
    m["page_retrieval"].numerator = int(usage.get("pages_ok") or 0)
    m["page_retrieval"].denominator = int(usage.get("pages_requested") or 0)

    # Extraction, exact precision, similar handling, price comparison: per candidate.
    x, ep, sm, pc = (m["extraction_accuracy"], m["exact_match_precision"], m["similar_match_handling"],
                     m["price_comparison_correctness"])  # fmt: skip
    for pid, p in products.items():
        entry = key.entry(pid)
        if states[pid] != "SEARCHED":
            for metric in (ep, sm, pc, x):
                metric.exclude(f"product {states[pid]}")
            continue
        intel = _intel_candidates(p)
        for c in p["matching"]["candidates"]:
            ic = intel.get(c["candidate_id"])
            ver = v.candidate(c["product_url"])
            status = c["match_status"]
            text = _text(c, ic)
            cond = (c.get("condition") or {}).get("condition")
            rules, why = rules_verdict(entry, text, cond, profiles.get(pid)) if entry else (IdentityVerdict.UNKNOWN, [])
            truth = ver.identity if ver and ver.identity is not IdentityVerdict.UNKNOWN else rules

            # Extraction accuracy (human-verified fields only; missing pages left out).
            if not (c.get("page") or {}).get("retrieved"):
                x.exclude("page not retrieved")
            elif (ic or {}).get("extraction_status") != "COMPLETED":
                x.exclude(f"extraction {(ic or {}).get('extraction_status')}")
            elif ver is None:
                x.exclude("not verified by a human")
            else:
                for fld, got in (("price", ic and ic.get("price")), ("currency", ic and ic.get("currency")),
                                 ("model", ic and ic.get("model")), ("availability", ic and ic.get("availability_status"))):
                    want = getattr(ver, fld)
                    if want is None:
                        continue
                    x.denominator += 1
                    same = (Decimal(str(got)) == Decimal(str(want))) if fld == "price" and got is not None else (
                        got is not None and str(got).strip().lower() == str(want).strip().lower())  # fmt: skip
                    if same:
                        x.numerator += 1
                    else:
                        x.failures.append(f"{c['product_url']}: {fld} extracted {got!r}, page shows {want!r}")

            # Exact-match precision: every EXACT_MATCH that can be judged.
            if status == "EXACT_MATCH":
                if truth is IdentityVerdict.UNKNOWN:
                    ep.exclude("identity not confirmed (rules or human)")
                else:
                    ep.denominator += 1
                    if truth is IdentityVerdict.EXACT:
                        ep.numerator += 1
                    else:
                        ep.failures.append(f"{c['product_url']}: EXACT_MATCH but {truth.value}"
                                           + (f" ({'; '.join(why)})" if why and truth is rules else ""))  # fmt: skip

            # Similar handling: pages that are a variant/condition mismatch must not be EXACT.
            if truth in (IdentityVerdict.VARIANT_MISMATCH, IdentityVerdict.AMBIGUOUS_BUNDLE):
                sm.denominator += 1
                if status == "EXACT_MATCH":
                    sm.failures.append(f"{c['product_url']}: variant mismatch labelled EXACT_MATCH")
                else:
                    sm.numerator += 1
                    sm.notes.append(f"{c['product_url']}: {status}")

            # Price comparison: every eligible comparison is re-checked from scratch.
            cmp_ = c.get("comparison") or {}
            if not cmp_.get("eligible"):
                continue
            if entry is not None and not entry.price_scoring_eligible:
                pc.exclude(f"{pid}: price scoring excluded by the answer key")
                continue
            pc.denominator += 1
            price = c.get("price") or {}
            problems = []
            if not c["competitor"].get("price_benchmark"):
                problems.append("competitor not benchmark-eligible")
            if status != "EXACT_MATCH":
                problems.append(f"match {status}")
            if truth not in (IdentityVerdict.EXACT, IdentityVerdict.UNKNOWN):
                problems.append(f"identity {truth.value}")
            if price.get("status") != "VALID":
                problems.append(f"price status {price.get('status')}")
            if price.get("currency") != "GBP":
                problems.append(f"currency {price.get('currency')}")
            if price.get("evidence_status") != "VERIFIED":
                problems.append(f"evidence {price.get('evidence_status')}")
            if not (c.get("condition") or {}).get("comparable_to_new"):
                problems.append("condition not comparable to new")
            try:
                diff_ok = Decimal(str(cmp_["difference_gbp"])) == Decimal(str(cmp_["competitor_price"])) - \
                    Decimal(str(cmp_["stompbox_price"]))  # fmt: skip
            except (KeyError, TypeError, ArithmeticError):
                diff_ok = False
            if not diff_ok:
                problems.append("difference is not competitor - Stompbox")
            base = (catalogue_prices or {}).get(pid)
            if base is not None and Decimal(str(cmp_.get("stompbox_price"))) != base:
                problems.append(f"Stompbox price {cmp_.get('stompbox_price')} != catalogue {base}")
            if ver is not None and ver.price is not None and price.get("price") is not None and \
                    Decimal(str(price["price"])) != ver.price:  # fmt: skip
                problems.append(f"price {price['price']} != page {ver.price}")
            if problems:
                pc.failures.append(f"{c['product_url']}: {', '.join(problems)}")
            else:
                pc.numerator += 1
            if ver is None or ver.price is None:
                pc.notes.append(f"{c['product_url']}: price not verified against the live page by a human")

    # Run completion.
    rc = m["run_completion"]
    for pid, p in products.items():
        rc.denominator += 1
        if p.get("status") == "COMPLETED":
            rc.numerator += 1
        else:
            rc.failures.append(f"{pid}: {p.get('status')} {p.get('error') or ''}".strip())
    rc.notes.append(f"run status {(run or {}).get('status')}")

    return Scorecard(run_id=(run or {}).get("run_id"), run_status=(run or {}).get("status"), product_states=states,
                     metrics={k: v_.dump() for k, v_ in m.items()},
                     cost_latency=cost_latency(result, ledger, duration_s, len(products)))  # fmt: skip


def cost_latency(result: dict, ledger: dict | None, duration_s: float | None, n_products: int) -> dict[str, Any]:
    entry = (ledger or {}).get("run") or {}
    disc = (result.get("discovery") or {}).get("usage") or {}
    intel = result.get("intelligence_usage") or {}
    charged = [entry.get("charged_exa_usd"), entry.get("charged_openrouter_usd")]
    total = sum((Decimal(str(c)) for c in charged if c is not None), Decimal(0)) if entry else None
    return {
        "duration_s": duration_s,
        "duration_per_product_s": round(duration_s / n_products, 2) if duration_s and n_products else None,
        "ledger_status": entry.get("status"),
        "reserved_usd": entry.get("reserved_usd"),
        "charged_exa_usd": entry.get("charged_exa_usd"),
        "charged_openrouter_usd": entry.get("charged_openrouter_usd"),
        "charged_total_usd": str(total) if total is not None else None,
        "charged_per_product_usd": str((total / n_products).quantize(Decimal("0.0001"))) if total and n_products else None,
        "unreported_attempts": entry.get("unreported_attempts"),
        "refused_attempts": entry.get("refused_attempts"),
        "pipeline_reported": {
            "discovery_search_usd": disc.get("search_cost_usd"), "discovery_llm_usd": disc.get("llm_cost_usd"),
            "search_usd": intel.get("search_cost_usd"), "retrieval_usd": intel.get("retrieval_cost_usd"),
            "llm2_usd": intel.get("llm_cost_usd"), "search_requests": (disc.get("search_requests") or 0)
            + (intel.get("search_requests") or 0), "llm_calls": (disc.get("llm_calls") or 0) + (intel.get("llm_calls") or 0),
        },
    }  # fmt: skip


def render_markdown(card: Scorecard) -> str:
    lines = [f"# Stage 9 scorecard: {card.run_id} ({card.run_status})", "", "| Metric | Score | Counts | Left out |",
             "|---|---|---|---|"]  # fmt: skip
    for name, mt in card.metrics.items():
        pct = "n/a" if mt["pct"] is None else f"{mt['pct']}%"
        left = ", ".join(f"{k}: {n}" for k, n in mt["excluded"].items()) or "-"
        lines.append(f"| {name} | {pct} | {mt['numerator']}/{mt['denominator']} | {left} |")
    lines += ["", "Product states: " + ", ".join(f"{k} {s}" for k, s in card.product_states.items()), ""]
    for name, mt in card.metrics.items():
        for f in mt["failures"]:
            lines.append(f"- **{name}** failure: {f}")
    lines += ["", "Cost and latency: " + ", ".join(f"{k}={v}" for k, v in card.cost_latency.items()
                                                    if k != "pipeline_reported")]  # fmt: skip
    return "\n".join(lines) + "\n"
