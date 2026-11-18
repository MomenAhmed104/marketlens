"""UK eligibility store integrity (audit fixes D1-D3): one canonical domain key, exceptions only
for INSUFFICIENT_EVIDENCE domains, and automatic, audited revocation on a status change.
Temporary databases only."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.uk_eligibility import UkConfidence, UkEligibilityProposal, UkProposalSource
from app.services.domains import canonical_domain
from app.services.review_store import ExceptionNotAllowedError, ReviewStore
from app.services.uk_gate import UkGateState, gate_decision
from tests.uk_fakes import AT, S, uk_exception, uk_record


@pytest.fixture
def store(tmp_path):
    return ReviewStore(tmp_path / "r.sqlite3")


def gate(store, domain):
    return gate_decision(domain, store.uk_gate_state())


# --------------------------------------------------------------------------- D1: canonical domain key


@pytest.mark.parametrize("raw", ["bax-shop.co.uk", "Bax-Shop.CO.UK", "www.bax-shop.co.uk", "WWW.Bax-Shop.co.uk",
                                 "  www.bax-shop.co.uk.  "])  # fmt: skip
def test_canonical_domain_normalises_case_www_whitespace_and_trailing_dot(raw):
    assert canonical_domain(raw) == "bax-shop.co.uk"


@pytest.mark.parametrize("raw", ["", "bax-shop", "https://www.bax-shop.co.uk", "bax-shop.co.uk/path",
                                 "bax-shop.co.uk:443", "user@bax-shop.co.uk", "shop.bax-shop.co.uk",
                                 "bax shop.co.uk", "-bax.co.uk", "bax..co.uk", "co.uk.evil.example", "co.uk",
                                 "www.co.uk"])  # fmt: skip
def test_canonical_domain_rejects_ambiguous_or_invalid_values(raw):
    with pytest.raises(ValueError):
        canonical_domain(raw)


def test_records_exceptions_and_proposals_store_the_canonical_domain():
    assert uk_record("WWW.Andertons.co.uk").domain == "andertons.co.uk"
    assert uk_exception("www.Gibson.COM").domain == "gibson.com"
    p = UkEligibilityProposal(domain="Www.Thomann.co.uk", status=S.NOT_UK_ELIGIBLE, confidence=UkConfidence.LOW,
                              source=UkProposalSource.AUDIT, proposed_at=AT)  # fmt: skip
    assert p.domain == "thomann.co.uk"


@pytest.mark.parametrize("raw", ["shop.thomann.co.uk", "https://thomann.co.uk/", "thomann"])
def test_models_reject_unnormalisable_domains(raw):
    with pytest.raises(ValidationError):
        uk_record(raw)
    with pytest.raises(ValidationError):
        uk_exception(raw)


def test_writes_and_reads_under_different_spellings_share_one_key(store):
    store.put_uk_eligibility(uk_record("WWW.Bax-Shop.co.uk", S.INSUFFICIENT_EVIDENCE))
    store.put_uk_exception(uk_exception("bax-shop.co.uk", search=True, benchmark=True))
    store.put_uk_eligibility(uk_record("www.bax-shop.co.uk", S.INSUFFICIENT_EVIDENCE, notes="re-reviewed"),
                             expected_version=1)  # fmt: skip
    assert store.get_uk_eligibility("BAX-SHOP.CO.UK").version == 2
    assert store.get_uk_exception("www.bax-shop.co.uk").allow_benchmark
    assert len(store.uk_eligibility_history("Www.Bax-Shop.co.uk")) == 2
    state = store.uk_gate_state()
    assert set(state.eligibility) == set(state.exceptions) == {"bax-shop.co.uk"}
    d = gate(store, "https://WWW.bax-shop.co.uk/some/product")
    assert d.search_allowed and d.benchmark_allowed and d.assessed


def test_invalid_lookup_domains_are_refused(store):
    with pytest.raises(ValueError):
        store.get_uk_eligibility("https://bax-shop.co.uk/")
    with pytest.raises(ValueError):
        store.uk_exception_history("shop.bax-shop.co.uk")


@pytest.mark.parametrize(("record_spelling", "exception_spelling"), [
    ("WWW.Thomann.co.uk", "thomann.co.uk"), ("thomann.co.uk", "www.THOMANN.co.uk"),
    ("Thomann.CO.UK", "www.thomann.co.uk"),
])  # fmt: skip
def test_not_uk_eligible_is_never_bypassed_by_an_exception_under_another_spelling(
        store, record_spelling, exception_spelling):  # fmt: skip
    # Order 1: the exception exists first (unassessed = INSUFFICIENT_EVIDENCE), then the NOT record.
    store.put_uk_exception(uk_exception(exception_spelling, search=True, benchmark=True))
    store.put_uk_eligibility(uk_record(record_spelling, S.NOT_UK_ELIGIBLE))
    for spelling in (record_spelling, exception_spelling, "thomann.co.uk", "https://www.thomann.co.uk/x"):
        d = gate(store, spelling)
        assert d.evidence_status is S.NOT_UK_ELIGIBLE and not d.search_allowed and not d.benchmark_allowed
    # Order 2: a new exception under any spelling is refused while the domain is NOT_UK_ELIGIBLE.
    with pytest.raises(ExceptionNotAllowedError):
        store.put_uk_exception(uk_exception(exception_spelling, search=True, benchmark=False))
    assert not store.get_uk_exception("thomann.co.uk").active


def test_gate_state_rekeys_defensively():
    # Even a model built without validation (model_construct) cannot land under a non-canonical key.
    r = uk_record("thomann.co.uk", S.NOT_UK_ELIGIBLE).model_construct(
        **{**uk_record("thomann.co.uk", S.NOT_UK_ELIGIBLE).model_dump(), "domain": "WWW.Thomann.co.uk"})
    e = uk_exception("thomann.co.uk", search=True, benchmark=True).model_construct(
        **{**uk_exception("thomann.co.uk", search=True, benchmark=True).model_dump(), "domain": "thomann.co.uk"})
    state = UkGateState.of([r], [e])
    assert set(state.eligibility) == {"thomann.co.uk"}
    assert not gate_decision("thomann.co.uk", state).search_allowed


# --------------------------------------------------------------------------- D2: exceptions need INSUFFICIENT_EVIDENCE


def test_exception_allowed_for_an_unassessed_domain(store):
    e = store.put_uk_exception(uk_exception("gibson.com", search=True, benchmark=False))
    assert e.version == 1 and e.approval == "EXPLICIT_USER_APPROVAL" and e.reviewer == "tester"
    d = gate(store, "gibson.com")
    assert d.search_allowed and not d.benchmark_allowed and not d.assessed


def test_exception_allowed_for_an_assessed_insufficient_domain(store):
    store.put_uk_eligibility(uk_record("guitarguitar.co.uk", S.INSUFFICIENT_EVIDENCE))
    store.put_uk_exception(uk_exception("guitarguitar.co.uk", search=True, benchmark=True))
    d = gate(store, "guitarguitar.co.uk")
    assert d.assessed and d.search_allowed and d.benchmark_allowed and d.search_basis == "EXCEPTION"


@pytest.mark.parametrize("status", [S.UK_ELIGIBLE, S.NOT_UK_ELIGIBLE])
@pytest.mark.parametrize(("search", "benchmark"), [(True, False), (True, True)])
def test_exception_refused_for_uk_eligible_and_not_uk_eligible(store, status, search, benchmark):
    store.put_uk_eligibility(uk_record("x.co.uk", status))
    with pytest.raises(ExceptionNotAllowedError) as e:
        store.put_uk_exception(uk_exception("x.co.uk", search=search, benchmark=benchmark))
    assert e.value.status is status and "INSUFFICIENT_EVIDENCE" in str(e.value)
    assert store.get_uk_exception("x.co.uk") is None and store.uk_exception_history("x.co.uk") == []


@pytest.mark.parametrize("status", [S.UK_ELIGIBLE, S.NOT_UK_ELIGIBLE])
def test_a_revocation_is_always_allowed(store, status):
    store.put_uk_eligibility(uk_record("x.co.uk", status))
    r = store.put_uk_exception(uk_exception("x.co.uk", search=False, benchmark=False, reason="revoked by hand"))
    assert not r.active and r.version == 1


def test_a_refused_exception_rolls_back_and_writes_no_history(store):
    store.put_uk_eligibility(uk_record("x.co.uk", S.INSUFFICIENT_EVIDENCE))
    store.put_uk_exception(uk_exception("x.co.uk", search=True))
    store.put_uk_eligibility(uk_record("x.co.uk", S.NOT_UK_ELIGIBLE))  # revokes (v2)
    with pytest.raises(ExceptionNotAllowedError):
        store.put_uk_exception(uk_exception("x.co.uk", search=True))
    assert [h.version for h in store.uk_exception_history("x.co.uk")] == [1, 2]


# --------------------------------------------------------------------------- D3: revocation on a status change


def test_status_change_revokes_and_a_return_to_insufficient_never_revives_the_exception(store):
    # 1. An exception is active on an insufficient-evidence domain.
    store.put_uk_eligibility(uk_record("bax-shop.co.uk", S.INSUFFICIENT_EVIDENCE))
    store.put_uk_exception(uk_exception("bax-shop.co.uk", search=True, benchmark=True))
    assert gate(store, "bax-shop.co.uk").benchmark_allowed

    # 2. The domain becomes UK_ELIGIBLE: allowed on evidence, and the exception is revoked atomically.
    later = AT + timedelta(days=1)
    store.put_uk_eligibility(uk_record("bax-shop.co.uk", S.UK_ELIGIBLE, reviewer="reviewer-2", decided_at=later),
                             expected_version=1)  # fmt: skip
    exc = store.get_uk_exception("bax-shop.co.uk")
    assert not exc.active and exc.revoked_by_status_change and exc.version == 2
    assert (exc.reviewer, exc.decided_at) == ("reviewer-2", later)
    assert "INSUFFICIENT_EVIDENCE to UK_ELIGIBLE" in exc.reason and "eligibility record v2" in exc.reason
    d = gate(store, "bax-shop.co.uk")
    assert d.search_basis == "UK_ELIGIBLE" and d.exception_version is None

    # 3. The domain later returns to INSUFFICIENT_EVIDENCE.
    store.put_uk_eligibility(uk_record("bax-shop.co.uk", S.INSUFFICIENT_EVIDENCE, decided_at=later + timedelta(1)),
                             expected_version=2)  # fmt: skip

    # 4. The original exception stays revoked: blocked until a new explicit approval.
    d = gate(store, "bax-shop.co.uk")
    assert not d.search_allowed and not d.benchmark_allowed and d.search_basis == "BLOCKED"
    hist = store.uk_exception_history("bax-shop.co.uk")
    assert [(h.version, h.allow_search, h.allow_benchmark, h.revoked_by_status_change) for h in hist] == [
        (1, True, True, False), (2, False, False, True)]  # fmt: skip
    assert hist[0].reason == "explicit test approval"  # the original approval stays in the audit trail

    store.put_uk_exception(uk_exception("bax-shop.co.uk", search=True, reason="re-approved"), expected_version=2)
    assert gate(store, "bax-shop.co.uk").search_allowed


def test_change_to_not_uk_eligible_revokes_the_exception(store):
    store.put_uk_exception(uk_exception("guitarguitar.co.uk", search=True, benchmark=True))  # unassessed
    store.put_uk_eligibility(uk_record("guitarguitar.co.uk", S.NOT_UK_ELIGIBLE))
    exc = store.get_uk_exception("guitarguitar.co.uk")
    assert not exc.active and exc.revoked_by_status_change
    assert "INSUFFICIENT_EVIDENCE to NOT_UK_ELIGIBLE" in exc.reason
    store.put_uk_eligibility(uk_record("guitarguitar.co.uk", S.INSUFFICIENT_EVIDENCE), expected_version=1)
    assert not gate(store, "guitarguitar.co.uk").search_allowed  # never revived


def test_same_status_update_keeps_the_exception(store):
    store.put_uk_exception(uk_exception("gibson.com", search=True))  # unassessed counts as INSUFFICIENT_EVIDENCE
    store.put_uk_eligibility(uk_record("gibson.com", S.INSUFFICIENT_EVIDENCE))
    store.put_uk_eligibility(uk_record("gibson.com", S.INSUFFICIENT_EVIDENCE, notes="more research"),
                             expected_version=1)  # fmt: skip
    exc = store.get_uk_exception("gibson.com")
    assert exc.active and exc.version == 1 and gate(store, "gibson.com").search_allowed


def test_status_change_without_an_active_exception_writes_no_exception(store):
    store.put_uk_eligibility(uk_record("x.co.uk", S.UK_ELIGIBLE))
    assert store.get_uk_exception("x.co.uk") is None
    store.put_uk_exception(uk_exception("y.co.uk", search=False, benchmark=False, reason="pre-emptive revoke"))
    store.put_uk_eligibility(uk_record("y.co.uk", S.UK_ELIGIBLE))
    assert store.get_uk_exception("y.co.uk").version == 1  # already inactive: nothing to revoke


def test_status_change_and_revocation_are_atomic(store, monkeypatch):
    store.put_uk_exception(uk_exception("x.co.uk", search=True))
    real = ReviewStore._write

    def failing(conn, table, history, record, expected_version, columns):
        if table == "uk_gate_exceptions":
            raise RuntimeError("simulated failure while revoking")
        return real(conn, table, history, record, expected_version, columns)

    monkeypatch.setattr(ReviewStore, "_write", staticmethod(failing))
    with pytest.raises(RuntimeError, match="simulated"):
        store.put_uk_eligibility(uk_record("x.co.uk", S.NOT_UK_ELIGIBLE))
    monkeypatch.undo()
    assert store.get_uk_eligibility("x.co.uk") is None and store.uk_eligibility_history("x.co.uk") == []
    assert store.get_uk_exception("x.co.uk").active  # nothing half-written


def test_a_version_conflict_revokes_nothing(store):
    store.put_uk_eligibility(uk_record("x.co.uk", S.INSUFFICIENT_EVIDENCE))
    store.put_uk_exception(uk_exception("x.co.uk", search=True))
    from app.services.review_store import VersionConflictError

    with pytest.raises(VersionConflictError):
        store.put_uk_eligibility(uk_record("x.co.uk", S.UK_ELIGIBLE), expected_version=0)
    assert store.get_uk_exception("x.co.uk").active and store.get_uk_eligibility("x.co.uk").version == 1


def test_automatic_revocation_flag_cannot_carry_active_scopes():
    with pytest.raises(ValidationError, match="automatic revocation"):
        uk_exception("x.co.uk", search=True, revoked_by_status_change=True)


def test_old_exception_json_without_the_new_field_still_loads():
    raw = uk_exception("x.co.uk").model_dump(exclude={"revoked_by_status_change"})
    assert "revoked_by_status_change" not in raw
    assert uk_exception("x.co.uk").model_validate(raw).revoked_by_status_change is False


def test_clock_is_irrelevant_to_revocation_time(store):
    """The revocation carries the eligibility record's decided_at, not wall-clock time."""
    store.put_uk_exception(uk_exception("x.co.uk", search=True))
    when = datetime(2027, 1, 2, 3, 4, tzinfo=UTC)
    store.put_uk_eligibility(uk_record("x.co.uk", S.UK_ELIGIBLE, decided_at=when))
    assert store.get_uk_exception("x.co.uk").decided_at == when
