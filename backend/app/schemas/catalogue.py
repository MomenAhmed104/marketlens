"""Stompbox catalogue product schema (source: workbook 'Products' sheet)."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class CatalogueProvenance(BaseModel):
    """How each catalogue row was researched. Preserved verbatim from the workbook."""

    model_config = ConfigDict(frozen=True)

    verification_status: str
    source_type: str
    evidence_url: HttpUrl
    last_checked: date
    price_note: str | None = None
    price_basis: str | None = None
    price_snapshot_date: date
    price_audit_status: str | None = None
    price_audit_note: str | None = None
    url_research_status: str | None = None


class StompboxProduct(BaseModel):
    model_config = ConfigDict(frozen=True)

    product_id: str = Field(pattern=r"^SB-\d{3}$")
    product_name: str = Field(min_length=1)
    brand: str = Field(min_length=1)
    manufacturer_model: str = Field(min_length=1)
    category: str = Field(min_length=1)
    subcategory: str = Field(min_length=1)
    # Stompbox's own SKU. May be a WooCommerce internal id (e.g. "child-6768");
    # it is NOT a manufacturer model number.
    stompbox_sku: str | None = None
    # Standard displayed price snapshot. None means missing (never coerced to 0).
    displayed_price: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    # Blank/"Unknown" both mean not verified; never inferred.
    condition: str | None = None
    availability: str | None = None
    # Free text exactly as captured; null means not yet collected.
    specifications: str | None = None
    product_url: HttpUrl
    search_keywords: str | None = None
    provenance: CatalogueProvenance

    @field_validator("displayed_price")
    @classmethod
    def _two_dp(cls, v: Decimal | None) -> Decimal | None:
        # Runs after the decimal_places check, so this never rounds real data.
        return v.quantize(Decimal("0.01")) if v is not None else None


class ProductListResponse(BaseModel):
    total: int
    items: list[StompboxProduct]


class ProductLookupRequest(BaseModel):
    product_ids: list[str] = Field(min_length=1, max_length=100)


class SubcategoryCount(BaseModel):
    category: str
    subcategory: str
    product_count: int
