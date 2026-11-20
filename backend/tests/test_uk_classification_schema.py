"""UK relevance evidence in LLM 1's classification output and on stored candidates (Option B).

LLM 1 returns quotes with their evidence kind and no UK verdict: `uk_relevance` and
`uk_confidence` are gone from the contract. A malformed UK answer must never fail the core
classification or the batch; core-field errors must still fail exactly as before. Older
stored records (with the retired category fields) still load. No LLM and no network.
"""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.agents.discovery.schemas import (
    ClassificationBatch,
    CompanyClassification,
    DiscoveredCompetitor,
    UkEvidenceAssessment,
    UkRelevanceQuote,
)
from app.agents.discovery.schemas import UkRelevanceEvidenceKind as K
from app.services.llm.base import strict_json_schema
from app.services.review_store import StoredCandidate

NOW = datetime(2026, 11, 18, tzinfo=UTC)
URL = "https://www.perfectpitch.co.uk/"


def core(**kw) -> dict:
    """A valid core classification as LLM 1 returns it (JSON-shaped)."""
    base = dict(domain="perfectpitch.co.uk", company_name="Perfect Pitch Ltd", company_type="specialist_retailer",
                relevant_categories=["Guitars"], uk_seller="yes", sells_new_musical_instruments=True,
                relevance=0.9, confidence=0.9, rationale="A music shop in Chesham.", evidence_urls=[URL],
                competitor_class="DIRECT_COMPETITOR")  # fmt: skip
    base.update(kw)
    return base


GOOD_UK = dict(uk_evidence=[{"url": URL, "quote": "your go-to musical instruments shop in Chesham",
                             "kind": "UK_PHYSICAL_STORE"}])  # fmt: skip


# --------------------------------------------------------------------------- strict output schema


def _defs(schema: dict) -> dict:
    return schema.get("$defs", {})


def test_strict_schema_requires_uk_evidence_and_no_uk_verdict():
    s = strict_json_schema(ClassificationBatch)
    company = _defs(s)["CompanyClassification"]
    assert "uk_evidence" in company["required"] and company["additionalProperties"] is False
    assert not {"uk_relevance", "uk_confidence"} & set(company["properties"])
    assert "UkRelevance" not in _defs(s) and "UkConfidence" not in _defs(s)
    quote = _defs(s)["UkRelevanceQuote"]
    assert quote["additionalProperties"] is False and set(quote["required"]) == {"url", "quote", "kind"}
    assert set(_defs(s)["UkRelevanceEvidenceKind"]["enum"]) == {k.value for k in K}


def test_strict_schema_exposes_no_private_or_internal_fields():
    company = _defs(strict_json_schema(ClassificationBatch))["CompanyClassification"]
    assert not {k for k in company["properties"] if k.startswith("_") or "warning" in k}


def test_strict_schema_has_no_length_limits_on_uk_fields():
    # A length limit violation would fail the whole batch; lengths are enforced by code instead.
    text = json.dumps(_defs(strict_json_schema(ClassificationBatch))["UkRelevanceQuote"])
    assert "maxLength" not in text and "minLength" not in text and "maxItems" not in text


# --------------------------------------------------------------------------- valid UK answers


def test_valid_uk_answer_is_parsed_without_warnings():
    c = CompanyClassification.model_validate(core(**GOOD_UK))
    assert c.uk_evidence == [UkRelevanceQuote(url=URL, quote="your go-to musical instruments shop in Chesham",
                                              kind=K.UK_PHYSICAL_STORE)]  # fmt: skip
    assert c.uk_validation_warnings == []


def test_old_style_construction_without_uk_fields_still_works():
    # Fixtures and legacy callers build classifications without UK fields: no evidence, no warning noise.
    c = CompanyClassification.model_validate(core())
    assert c.uk_evidence == [] and c.uk_validation_warnings == []


def test_legacy_uk_verdict_keys_are_dropped_and_never_trusted():
    c = CompanyClassification.model_validate(core(uk_relevance="UK_BASED", uk_confidence="HIGH", **GOOD_UK))
    dumped = c.model_dump()
    assert "uk_relevance" not in dumped and "uk_confidence" not in dumped
    assert c.uk_validation_warnings == [] and len(c.uk_evidence) == 1


def test_core_fields_are_unchanged_by_the_uk_cleaning():
    a = CompanyClassification.model_validate(core())
    b = CompanyClassification.model_validate(core(uk_relevance="nonsense", uk_evidence="oops"))
    assert a.model_dump(exclude={"uk_evidence"}) == b.model_dump(exclude={"uk_evidence"})


# --------------------------------------------------------------------------- malformed UK answers are isolated


@pytest.mark.parametrize("value", ["a string", 42, {"url": URL}])
def test_uk_evidence_that_is_not_a_list_becomes_empty(value):
    c = CompanyClassification.model_validate(core(uk_evidence=value))
    assert c.uk_evidence == [] and any("not a list" in w for w in c.uk_validation_warnings)


def test_null_uk_evidence_counts_as_missing():
    c = CompanyClassification.model_validate(core(uk_evidence=None))
    assert c.uk_evidence == [] and any("uk_evidence missing" in w for w in c.uk_validation_warnings)


def test_malformed_quote_items_are_dropped_and_valid_ones_kept():
    items = [
        {"url": URL, "quote": "your go-to musical instruments shop in Chesham", "kind": "UK_PHYSICAL_STORE"},
        {"url": URL, "quote": "x", "kind": "NOT_A_KIND"},  # bad kind
        {"url": URL, "kind": "UK_ADDRESS"},  # no quote
        "just a string",
        None,
        {"url": 5, "quote": ["list"], "kind": "UK_ADDRESS"},  # wrong types
    ]
    c = CompanyClassification.model_validate(core(uk_evidence=items))
    assert [q.kind for q in c.uk_evidence] == [K.UK_PHYSICAL_STORE]
    dropped = [w for w in c.uk_validation_warnings if "dropped" in w]
    assert [w.split("]")[0] for w in dropped] == ["uk_evidence[1", "uk_evidence[2", "uk_evidence[3",
                                                  "uk_evidence[4", "uk_evidence[5"]  # fmt: skip


def test_quote_items_with_extra_keys_keep_only_the_known_fields():
    item = {"url": URL, "quote": "your go-to musical instruments shop in Chesham", "kind": "UK_PHYSICAL_STORE",
            "verified": True, "supports": True}  # an LLM cannot pre-verify its own quote  # fmt: skip
    c = CompanyClassification.model_validate(core(uk_evidence=[item]))
    assert type(c.uk_evidence[0]) is UkRelevanceQuote and not hasattr(c.uk_evidence[0], "verified")


def test_bad_uk_answer_on_one_company_does_not_fail_the_batch():
    batch = ClassificationBatch.model_validate({"companies": [
        core(**GOOD_UK),
        core(domain="other.co.uk", uk_evidence=[{"bad": True}]),
    ]})  # fmt: skip
    first, second = batch.companies
    assert len(first.uk_evidence) == 1 and first.uk_validation_warnings == []
    assert (second.domain, second.uk_evidence) == ("other.co.uk", [])
    assert len(second.uk_validation_warnings) == 1


def test_batch_parsed_from_json_text_like_the_openrouter_client():
    raw = json.dumps({"companies": [core(uk_evidence=[{"url": URL, "quote": 1, "kind": "UK_DELIVERY"}])]})
    c = ClassificationBatch.model_validate(json.loads(raw)).companies[0]
    assert c.uk_evidence == []


# --------------------------------------------------------------------------- core failures behave as before


@pytest.mark.parametrize("bad", [
    {"company_type": "shop"},
    {"relevance": 1.5},
    {"confidence": "high"},
    {"uk_seller": "maybe"},
    {"evidence_urls": "https://x"},
    {"competitor_class": "BEST"},
])
def test_core_field_errors_still_fail_validation(bad):
    with pytest.raises(ValidationError):
        CompanyClassification.model_validate(core(**GOOD_UK, **bad))


@pytest.mark.parametrize("missing", ["domain", "company_type", "rationale", "evidence_urls"])
def test_missing_core_fields_still_fail_validation(missing):
    data = core(**GOOD_UK)
    data.pop(missing)
    with pytest.raises(ValidationError):
        CompanyClassification.model_validate(data)


def test_core_failure_on_one_company_still_fails_the_whole_batch():
    with pytest.raises(ValidationError):
        ClassificationBatch.model_validate({"companies": [core(**GOOD_UK), core(company_type="shop")]})


def test_existing_instance_keeps_its_warnings_when_revalidated():
    c = CompanyClassification.model_validate(core(uk_evidence="bad"))
    batch = ClassificationBatch(companies=[c])
    assert batch.companies[0].uk_validation_warnings == c.uk_validation_warnings != []


# --------------------------------------------------------------------------- stored candidates


def candidate(**kw) -> DiscoveredCompetitor:
    return DiscoveredCompetitor(domain="perfectpitch.co.uk", hostnames=["www.perfectpitch.co.uk"],
                                homepage_url=URL, evidence=[], products_matched=[], uk_signals=[],
                                discovered_at=NOW, **kw)  # fmt: skip


def test_new_candidates_are_not_assessed_by_default():
    assert candidate().uk_assessment is None


def test_assessment_round_trips_through_json():
    a = UkEvidenceAssessment(sufficient=True, contradicted=True, mixed_location=False, independent_sources=2,
                             reasons=["Verified UK presence", "Contradiction"])  # fmt: skip
    again = DiscoveredCompetitor.model_validate_json(candidate(uk_assessment=a).model_dump_json())
    assert again.uk_assessment == a


def test_assessment_stored_with_the_retired_category_loads_without_trusting_it():
    a = UkEvidenceAssessment.model_validate({"proposed": "UK_BASED", "relevance": "UK_BASED", "confidence": "HIGH",
                                             "proposed_confidence": "HIGH"})  # fmt: skip
    assert (a.sufficient, a.contradicted, a.independent_sources, a.evidence) == (False, False, 0, [])
    assert "relevance" not in a.model_dump()


def test_records_stored_before_uk_evidence_load_with_no_assessment():
    stored = {"domain": "perfectpitch.co.uk", "first_seen_at": NOW.isoformat(), "last_seen_at": NOW.isoformat(),
              "last_run_id": "discovery-x", "times_seen": 1, "discovery_mode": "LLM_DISABLED",
              "candidate": json.loads(candidate().model_dump_json(exclude={"uk_assessment"}))}  # fmt: skip
    assert "uk_assessment" not in stored["candidate"]
    loaded = StoredCandidate.model_validate_json(json.dumps(stored))
    assert loaded.candidate.uk_assessment is None
