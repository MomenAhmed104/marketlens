"""Synthetic, controlled Phase 5 candidates for matching tests (no network, no LLM)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.agents.discovery.schemas import SearchTarget
from app.agents.intelligence.schemas import (
    CompetitorProduct,
    ExtractedField,
    ExtractionStatus,
    FieldEvidence,
    FieldStatus,
    PageRecord,
    PageType,
    SearchHit,
)
from app.schemas.common import DomainApprovalStatus
from app.schemas.competitor import CompanyType
from app.schemas.search import RetrievalStatus
from app.services.urls import canonical_url

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def target(domain="andertons.co.uk", ctype=CompanyType.RETAILER, benchmark=True,
           status=DomainApprovalStatus.PRECONFIGURED) -> SearchTarget:  # fmt: skip
    """A search target as a Phase 5 run carries it (benchmark flag from the workbook or reviewer)."""
    return SearchTarget(domain=domain, company_name=None, company_type=ctype, approval_status=status,
                        price_benchmark=benchmark)  # fmt: skip


def candidate(
    url: str,
    title: str | None = None,
    *,
    page_title: str | None = "same",
    page_status: RetrievalStatus | None = RetrievalStatus.OK,
    llm: bool = False,
    page_type: PageType | None = PageType.PRODUCT_PAGE,
    product_title: str | None = None,
    brand: str | None = None,
    model: str | None = None,
    sku: str | None = None,
    availability: str | None = None,
    price: str | None = None,  # e.g. "189.00"
    currency: str | None = "GBP",
    price_text: str | None = None,  # defaults to £<price>
    price_status: FieldStatus | None = None,  # Phase 5 field status; default EXTRACTED when price given
    quote: str | None = None,
    sale: bool = False,
    competitor: SearchTarget | None = None,
    retrieved_at: datetime = NOW,
    extraction: ExtractionStatus | None = None,
) -> CompetitorProduct:
    """A Phase 5 candidate. Without `llm`, it looks like no-LLM mode (SKIPPED_NO_LLM)."""
    page_title = title if page_title == "same" else page_title
    ok = page_status is RetrievalStatus.OK
    status = extraction or (ExtractionStatus.COMPLETED if llm and ok else
                            ExtractionStatus.SKIPPED_NO_LLM if ok else ExtractionStatus.SKIPPED_NOT_RETRIEVED)  # fmt: skip
    c = CompetitorProduct(
        candidate_id=f"SB-x:{abs(hash(url)) % 10**12}", stompbox_product_id="SB-x", competitor=competitor or target(),
        product_url=canonical_url(url),
        search=SearchHit(domain=(competitor or target()).domain, query_text="q", provider="exa",
                         provider_request_id="s-1", rank=1, url=url, canonical_url=canonical_url(url),
                         hostname="x", title=title, snippet=None, published_raw=None, retrieved_at=retrieved_at,
                         mentions_model=False, mentions_brand=False),
        page=PageRecord(status=page_status, provider="exa", requested_url=canonical_url(url),
                        final_url=canonical_url(url) if ok else None, title=page_title if ok else None,
                        content_chars=5000 if ok else 0, retrieved_at=retrieved_at if page_status else None),
        extraction_status=status,
    )  # fmt: skip
    if status is not ExtractionStatus.COMPLETED:
        return c
    c.page_type = page_type
    evidence = []
    for f, v in ((ExtractedField.PRODUCT_TITLE, product_title), (ExtractedField.BRAND, brand),
                 (ExtractedField.MODEL, model), (ExtractedField.SKU, sku), (ExtractedField.AVAILABILITY, availability)):
        evidence.append(FieldEvidence(field=f, status=FieldStatus.EXTRACTED if v else FieldStatus.NOT_FOUND,
                                      value=v, quote=v))  # fmt: skip
    c.product_title, c.brand, c.model, c.sku, c.availability = product_title, brand, model, sku, availability
    symbol = {"GBP": "£", "EUR": "€", "USD": "US$"}.get(currency or "", "")
    text = price_text or (f"{symbol}{price}" if price else None)
    pstatus = price_status or (FieldStatus.EXTRACTED if price else FieldStatus.NOT_FOUND)
    evidence.append(FieldEvidence(field=ExtractedField.PRICE, status=pstatus, value=text,
                                  quote=quote if quote is not None else text))  # fmt: skip
    if pstatus is FieldStatus.EXTRACTED:
        c.price, c.currency, c.price_text = Decimal(price), currency, text
    c.field_evidence = evidence
    c.sale_indicated = sale
    c.llm_confidence = 0.9
    return c
