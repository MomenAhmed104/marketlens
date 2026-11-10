"""Status enums shared across pipeline stages.

Defined up front so every later phase uses the same vocabulary (see CLAUDE.md §4).
"""

from enum import StrEnum


class MatchStatus(StrEnum):
    EXACT = "EXACT"
    SIMILAR = "SIMILAR"
    REVIEW = "REVIEW"
    NOT_MATCHED = "NOT_MATCHED"


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
