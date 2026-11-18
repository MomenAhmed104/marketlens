"""Deterministic checks on UK eligibility evidence before it is stored.

`SAVED_EVIDENCE` items claim to come from evidence discovery already saved for the
domain (the review queue). That claim is verified here, never trusted: the URL must be
one of the domain's stored evidence items and the quote must appear verbatim
(whitespace and case aside) in that item's stored title or snippet. Reviewer-supplied
evidence (e.g. a Companies House page) cannot be checked against saved data; the
model requires a note saying how and when it was read.
"""

from __future__ import annotations

from app.schemas.uk_eligibility import UkEvidence, UkEvidenceSource
from app.services.review_store import StoredCandidate
from app.services.urls import canonical_url


def _norm(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def _canonical(url: str) -> str:
    try:
        return canonical_url(url)
    except ValueError:
        return url


def saved_evidence_problems(evidence: list[UkEvidence], stored: StoredCandidate | None) -> list[str]:
    """One message per SAVED_EVIDENCE item that is not backed by the domain's stored evidence."""
    problems: list[str] = []
    items = stored.candidate.evidence if stored else []
    by_url: dict[str, list] = {}
    for item in items:
        for key in {_canonical(item.url), item.canonical_url}:
            by_url.setdefault(key, []).append(item)
    for i, e in enumerate(evidence):
        if e.source is not UkEvidenceSource.SAVED_EVIDENCE:
            continue
        if stored is None:
            problems.append(f"evidence[{i}]: SAVED_EVIDENCE needs saved discovery evidence for this domain; "
                            "there is none (use REVIEWER_SUPPLIED with a note)")  # fmt: skip
            continue
        matches = by_url.get(_canonical(e.url), [])
        if not matches:
            problems.append(f"evidence[{i}]: {e.url} is not one of this domain's saved evidence items")
        elif not any(_norm(e.quote) in _norm(m.title) or _norm(e.quote) in _norm(m.snippet) for m in matches):
            problems.append(f"evidence[{i}]: the quote does not appear in the saved title or snippet for {e.url}")
    return problems
