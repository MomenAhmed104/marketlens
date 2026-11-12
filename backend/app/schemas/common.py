"""Status enums shared across pipeline stages.

Defined up front so every later phase uses the same vocabulary (see CLAUDE.md §4).
"""

from enum import StrEnum


class MatchStatus(StrEnum):
    EXACT_MATCH = "EXACT_MATCH"  # same product identity and required variants
    SIMILAR_MATCH = "SIMILAR_MATCH"  # related alternative, not the same product/offer
    REVIEW_REQUIRED = "REVIEW_REQUIRED"  # possible match with unresolved identity/variant evidence
    NO_MATCH = "NO_MATCH"  # evidence does not support a match


class EvidenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    STALE = "STALE"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"


class DomainApprovalStatus(StrEnum):
    PRECONFIGURED = "PRECONFIGURED"  # supplied in the catalogue workbook
    AUTO_APPROVED = "AUTO_APPROVED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
