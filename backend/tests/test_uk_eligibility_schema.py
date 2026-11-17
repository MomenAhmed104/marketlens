"""UK eligibility models and the central gate decision. Pure; no database, no network."""

import pytest
from pydantic import ValidationError

from app.schemas.uk_eligibility import (
    UkEligibilityStatus,
    UkEvidence,
    UkEvidenceKind,
    UkEvidenceSource,
    UkGateBasis,
)
from app.services.uk_gate import UkGateState, benchmark_block_reason, blocked_reason, gate_decision
from tests.uk_fakes import strong, uk_exception, uk_record, uk_state

S, K = UkEligibilityStatus, UkEvidenceKind


def weak(kind=K.WEAK_SIGNAL) -> UkEvidence:
    return UkEvidence(url="https://shop.example/delivery", quote="Free UK delivery over £50", kind=kind,
                      source=UkEvidenceSource.REVIEWER_SUPPLIED, note="read in a test")  # fmt: skip


# --------------------------------------------------------------------------- record validation


@pytest.mark.parametrize("kind", [K.PHYSICAL_STORE, K.REGISTERED_COMPANY, K.UK_OPERATING_ADDRESS,
                                  K.UK_RETAIL_OPERATION])  # fmt: skip
def test_uk_eligible_accepts_each_strong_evidence_kind(kind):
    assert uk_record("a.co.uk", evidence=[strong(kind=kind)]).status is S.UK_ELIGIBLE


@pytest.mark.parametrize("evidence", [[], [weak()], [weak(K.OTHER)], [weak(), weak(K.OTHER)]])
def test_uk_eligible_refuses_weak_or_missing_evidence(evidence):
    with pytest.raises(ValidationError, match="strong evidence"):
        uk_record("a.co.uk", evidence=evidence)


def test_not_uk_eligible_needs_evidence_and_insufficient_does_not():
    with pytest.raises(ValidationError, match="NOT_UK_ELIGIBLE needs"):
        uk_record("a.co.uk", S.NOT_UK_ELIGIBLE, evidence=[])
    assert uk_record("a.co.uk", S.NOT_UK_ELIGIBLE).evidence[0].kind is K.NON_UK_OPERATOR
    assert uk_record("a.co.uk", S.INSUFFICIENT_EVIDENCE).evidence == []


def test_reviewer_supplied_evidence_needs_a_note_and_an_http_url():
    with pytest.raises(ValidationError, match="needs a note"):
        UkEvidence(url="https://x.example/a", quote="q", kind=K.PHYSICAL_STORE, source=UkEvidenceSource.REVIEWER_SUPPLIED)
    with pytest.raises(ValidationError, match="http"):
        UkEvidence(url="javascript:alert(1)", quote="q", kind=K.PHYSICAL_STORE, source=UkEvidenceSource.SAVED_EVIDENCE)
    ok = UkEvidence(url="https://x.example/a", quote="  a   b ", kind=K.OTHER, source=UkEvidenceSource.SAVED_EVIDENCE)
    assert ok.quote == "a b" and ok.note is None


def test_records_are_frozen_and_reject_unknown_fields():
    r = uk_record("a.co.uk")
    with pytest.raises(ValidationError):
        r.status = S.NOT_UK_ELIGIBLE
    with pytest.raises(ValidationError):
        uk_record("a.co.uk", search_allowed=True)


def test_exception_benchmark_needs_search_and_records_explicit_approval():
    with pytest.raises(ValidationError, match="needs a search exception"):
        uk_exception("a.co.uk", search=False, benchmark=True)
    e = uk_exception("a.co.uk", search=True, benchmark=False)
    assert e.approval == "EXPLICIT_USER_APPROVAL" and e.active
    assert not uk_exception("a.co.uk", search=False, benchmark=False).active  # a revocation


# --------------------------------------------------------------------------- gate decision


def test_unassessed_domain_is_insufficient_evidence_and_blocked():
    d = gate_decision("andertons.co.uk", UkGateState.empty())
    assert (d.evidence_status, d.assessed, d.search_allowed, d.benchmark_allowed) == (
        S.INSUFFICIENT_EVIDENCE, False, False, False)  # fmt: skip
    assert d.search_basis is d.benchmark_basis is UkGateBasis.BLOCKED
    assert "not assessed" in d.reasons[0] and "not assessed" in blocked_reason(d)


def test_uk_eligible_allows_search_and_benchmark():
    d = gate_decision("andertons.co.uk", uk_state(["andertons.co.uk"]))
    assert d.assessed and d.search_allowed and d.benchmark_allowed and d.reasons == []
    assert d.search_basis is UkGateBasis.UK_ELIGIBLE
    assert benchmark_block_reason(d, "andertons.co.uk") is None


@pytest.mark.parametrize("status", [S.INSUFFICIENT_EVIDENCE, S.NOT_UK_ELIGIBLE])
def test_assessed_but_not_eligible_is_blocked(status):
    d = gate_decision("x.co.uk", uk_state(records=[uk_record("x.co.uk", status)]))
    assert d.assessed and not d.search_allowed and not d.benchmark_allowed
    assert d.evidence_status is status and status.value in d.reasons[0]


def test_search_only_exception_allows_search_but_never_benchmark():
    d = gate_decision("gibson.com", uk_state(exceptions=[uk_exception("gibson.com", search=True, benchmark=False)]))
    assert d.evidence_status is S.INSUFFICIENT_EVIDENCE  # the evidence status is unchanged
    assert d.search_allowed and not d.benchmark_allowed
    assert (d.search_basis, d.benchmark_basis) == (UkGateBasis.EXCEPTION, UkGateBasis.BLOCKED)
    assert d.exception_version == 1 and "not a UK price benchmark" in benchmark_block_reason(d, "gibson.com")


def test_search_and_benchmark_exception_on_an_assessed_insufficient_domain():
    state = uk_state(records=[uk_record("bax-shop.co.uk", S.INSUFFICIENT_EVIDENCE)],
                     exceptions=[uk_exception("bax-shop.co.uk", search=True, benchmark=True)])  # fmt: skip
    d = gate_decision("bax-shop.co.uk", state)
    assert d.evidence_status is S.INSUFFICIENT_EVIDENCE and d.assessed
    assert d.search_allowed and d.benchmark_allowed and d.benchmark_basis is UkGateBasis.EXCEPTION


def test_exception_never_applies_to_not_uk_eligible():
    state = uk_state(records=[uk_record("thomann.co.uk", S.NOT_UK_ELIGIBLE)],
                     exceptions=[uk_exception("thomann.co.uk", search=True, benchmark=True)])  # fmt: skip
    d = gate_decision("thomann.co.uk", state)
    assert not d.search_allowed and not d.benchmark_allowed
    assert any("Exception ignored" in r for r in d.reasons)


def test_revoked_exception_blocks_again():
    d = gate_decision("x.co.uk", uk_state(exceptions=[uk_exception("x.co.uk", search=False, benchmark=False)]))
    assert not d.search_allowed and not d.benchmark_allowed


def test_gate_normalises_hostnames_to_the_registrable_domain():
    state = uk_state(["andertons.co.uk"])
    assert gate_decision("WWW.Andertons.co.uk", state).search_allowed
    assert gate_decision("andertons.co.uk.evil.example", state).search_allowed is False


def test_missing_gate_decision_is_never_a_benchmark():
    assert "unknown" in benchmark_block_reason(None, "a.co.uk")
