"""Product image metadata (MarketLens product images).

An image is a reference, never a copy: the record keeps the image URL, the page it was found
on, how it was found and whether it could be used. Images are display-only evidence of what a
listing looks like. They are never used to decide a product match.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ImageStatus(StrEnum):
    FOUND = "FOUND"  # a product image URL was extracted and validated as an image
    NOT_FOUND = "NOT_FOUND"  # the page was read but no product image was identified
    INVALID = "INVALID"  # a candidate URL was found but did not validate as a usable image
    BLOCKED = "BLOCKED"  # the page refused automated access (403/429, bot check)
    FETCH_FAILED = "FETCH_FAILED"  # network error, timeout, non-HTML or oversized page
    NOT_ATTEMPTED = "NOT_ATTEMPTED"  # image lookup disabled, or the page was not retrieved


class ImageSource(StrEnum):
    """Where on the page the primary image came from, in extraction priority order."""

    JSON_LD = "JSON_LD"  # Schema.org Product image in structured data
    OPEN_GRAPH = "OPEN_GRAPH"  # og:image / og:image:secure_url
    PRODUCT_META = "PRODUCT_META"  # product image metadata: itemprop=image, twitter:image, image_src
    PAGE_IMAGE = "PAGE_IMAGE"  # a product-gallery <img> identified by its markup


class ProductImage(BaseModel):
    """One listing's primary image plus traceability. `url` is set only when it was really on the page."""

    status: ImageStatus
    url: str | None = None  # absolute http(s) image URL exactly as resolved from the page
    source_page_url: str | None = None  # the product page the image was read from
    source: ImageSource | None = None
    additional_urls: list[str] = Field(default_factory=list)  # further images of the same listing, if any
    retrieved_at: datetime | None = None
    note: str | None = None  # why it is not FOUND, or what was skipped

    @property
    def usable(self) -> bool:
        return self.status is ImageStatus.FOUND and self.url is not None
