"""Saved runs written before the Phase 5 finalisation (12 Nov 2026) still load.

Those runs used extraction_status SKIPPED_LLM_DISABLED, the earlier name of SKIPPED_NO_LLM
(retrieved page, LLM 2 not configured or disabled, nothing extracted). Loading is read-only:
the alias is never written and saved files are never touched.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from app.agents.intelligence.schemas import (
    LEGACY_EXTRACTION_STATUS,
    CompetitorProduct,
    ExtractionStatus,
    IntelligenceRunResult,
)
from app.services.matching import evaluate_run

FIXTURES = Path(__file__).parent / "fixtures"
LEGACY = FIXTURES / "legacy_skipped_llm_disabled_run.json"
CURRENT = FIXTURES / "phase5_no_llm_run.json"


def load(path: Path) -> IntelligenceRunResult:
    return IntelligenceRunResult.model_validate_json(path.read_text(encoding="utf-8"))


def test_the_legacy_fixture_really_uses_the_old_value():
    raw = json.loads(LEGACY.read_text(encoding="utf-8"))
    statuses = {c["extraction_status"] for p in raw["products"] for c in p["candidates"]}
    assert statuses == {"SKIPPED_LLM_DISABLED"}


def test_legacy_status_loads_as_its_current_equivalent_with_the_original_kept_visible():
    before = LEGACY.read_bytes()
    run = load(LEGACY)
    cands = run.products[0].candidates
    assert cands and all(c.extraction_status is ExtractionStatus.SKIPPED_NO_LLM for c in cands)
    # The original value is not lost: the explanation names it.
    assert all("legacy status SKIPPED_LLM_DISABLED" in c.extraction_error for c in cands)
    assert all(c.page.status.value == "OK" for c in cands)  # same meaning: page retrieved, not read
    assert LEGACY.read_bytes() == before  # read-only


def test_legacy_status_also_loads_from_python_data_and_keeps_an_existing_explanation():
    raw = json.loads(LEGACY.read_text(encoding="utf-8"))["products"][0]["candidates"][0]
    c = CompetitorProduct.model_validate(raw)
    assert c.extraction_status is ExtractionStatus.SKIPPED_NO_LLM
    raw_with_reason = {**raw, "extraction_error": "LLM 2 not used (LLM2_MODEL not set)"}
    assert CompetitorProduct.model_validate(raw_with_reason).extraction_error == "LLM 2 not used (LLM2_MODEL not set)"
    assert raw["extraction_status"] == "SKIPPED_LLM_DISABLED"  # input dict not modified


def test_a_legacy_run_is_written_back_only_with_current_values():
    dumped = load(LEGACY).model_dump_json()
    assert "SKIPPED_NO_LLM" in dumped and '"extraction_status":"SKIPPED_LLM_DISABLED"' not in dumped


def test_the_alias_is_narrow():
    assert set(LEGACY_EXTRACTION_STATUS) == {"SKIPPED_LLM_DISABLED"}
    for bad in ("SKIPPED", "skipped_llm_disabled", "SKIPPED_LLM_DISABLED ", "UNKNOWN"):
        with pytest.raises(ValueError):
            ExtractionStatus(bad)

    class M(BaseModel):
        s: ExtractionStatus

    with pytest.raises(ValidationError):
        M.model_validate({"s": "NOT_A_STATUS"})
    # The alias is not advertised: schemas (and the LLM contract) list current values only.
    assert "SKIPPED_LLM_DISABLED" not in json.dumps(M.model_json_schema())


def test_current_format_runs_are_unchanged():
    run = load(CURRENT)
    cands = [c for p in run.products for c in p.candidates]
    raw = json.loads(CURRENT.read_text(encoding="utf-8"))
    raw_cands = [c for p in raw["products"] for c in p["candidates"]]
    assert [c.extraction_status.value for c in cands] == [c["extraction_status"] for c in raw_cands]
    assert [c.extraction_error for c in cands] == [c.get("extraction_error") for c in raw_cands]


def test_matching_treats_a_legacy_run_exactly_like_its_current_form(catalogue):
    legacy = load(LEGACY)
    current_raw = json.loads(LEGACY.read_text(encoding="utf-8"))
    for p in current_raw["products"]:
        for c in p["candidates"]:
            c["extraction_status"] = "SKIPPED_NO_LLM"
    current = IntelligenceRunResult.model_validate(current_raw)
    now = datetime(2026, 11, 12, 18, tzinfo=UTC)
    a = evaluate_run(legacy, catalogue.products, 24, now=now)
    b = evaluate_run(current, catalogue.products, 24, now=now)
    key = lambda r: [(m.product_url, m.match_status, m.extraction_status, m.price.status)  # noqa: E731
                     for s in r.products for m in s.candidates]  # fmt: skip
    assert key(a) and key(a) == key(b)
