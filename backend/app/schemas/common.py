"""Status enums shared across pipeline stages.

Defined up front so every later phase uses the same vocabulary (see CLAUDE.md §4).
"""

from enum import StrEnum


class MatchStatus(StrEnum):
    EXACT_MATCH = "EXACT_MATCH"  # same product identity and required variants
    # Otherwise the same product; only the cosmetic finish/colour differs (decided in
    # Python, never by LLM 2). Not a direct price benchmark: an alternative-product price.
    EQUIVALENT_VARIANT = "EQUIVALENT_VARIANT"
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


class AvailabilityStatus(StrEnum):
    """Normalised stock status, set deterministically from verified page text.

    UNKNOWN covers missing, ambiguous, conflicting or unverifiable evidence. Not
    finding a page never makes a product unavailable.
    """

    IN_STOCK = "IN_STOCK"  # in stock / buyable now (incl. explicit low-stock wording on a buyable page)
    OUT_OF_STOCK = "OUT_OF_STOCK"
    PREORDER = "PREORDER"  # orderable before release
    BACKORDER = "BACKORDER"  # orderable now for later fulfilment
    DISCONTINUED = "DISCONTINUED"
    UNKNOWN = "UNKNOWN"
