"""Load and validate the Stompbox catalogue workbook (products + competitors).

All checks here are deterministic. Loading fails loudly (CatalogueLoadError,
with row numbers) rather than silently skipping or repairing bad rows.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from pydantic import ValidationError

from app.schemas.catalogue import CatalogueProvenance, StompboxProduct, SubcategoryCount
from app.schemas.common import DomainApprovalStatus
from app.schemas.competitor import (
    CompanyType,
    CompetitorDomain,
    CompetitorPriority,
    CompetitorSource,
)
from app.services.domains import normalize_hostname

PRODUCT_COLUMNS = (
    "product_id", "product_name", "brand", "manufacturer_model", "category",
    "subcategory", "stompbox_sku", "displayed_price", "currency", "condition",
    "availability", "specifications", "product_url", "search_keywords",
    "verification_status", "evidence_url", "last_checked", "price_note",
    "source_type", "url_research_status", "price_audit_status",
    "price_audit_note", "price_basis", "price_snapshot_date",
)  # fmt: skip
PROVENANCE_COLUMNS = tuple(CatalogueProvenance.model_fields)
COMPETITOR_COLUMNS = (
    "competitor_id", "competitor_name", "domain_url", "priority", "classification", "notes",
)  # fmt: skip

# Explicit mapping of the workbook's free-text classifications. An unmapped value
# is an error, so a new classification is never silently guessed.
CLASSIFICATION_TO_COMPANY_TYPE: dict[str, CompanyType] = {
    "Primary retailer": CompanyType.RETAILER,
    "Selective retailer": CompanyType.RETAILER,
    "Specialist retailer": CompanyType.SPECIALIST_RETAILER,
    "Used / vintage specialist": CompanyType.SPECIALIST_RETAILER,
    "Manufacturer / brand-specific retail": CompanyType.MANUFACTURER,
    "Manufacturer": CompanyType.MANUFACTURER,
}


class CatalogueLoadError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Catalogue failed validation:\n  " + "\n  ".join(errors))


@dataclass(frozen=True)
class DuplicateCompetitor:
    domain: str
    kept_id: str | None
    dropped_id: str | None
    dropped_name: str


@dataclass
class Catalogue:
    source_path: Path
    products: dict[str, StompboxProduct]
    competitors: list[CompetitorDomain]
    duplicate_competitors: list[DuplicateCompetitor] = field(default_factory=list)

    def search(
        self,
        q: str | None = None,
        category: str | None = None,
        subcategory: str | None = None,
        brand: str | None = None,
    ) -> list[StompboxProduct]:
        terms = q.casefold().split() if q else []

        def keep(p: StompboxProduct) -> bool:
            if category and p.category.casefold() != category.casefold():
                return False
            if subcategory and p.subcategory.casefold() != subcategory.casefold():
                return False
            if brand and p.brand.casefold() != brand.casefold():
                return False
            if terms:
                haystack = " ".join(
                    filter(None, (p.product_id, p.product_name, p.brand,
                                  p.manufacturer_model, p.stompbox_sku, p.search_keywords))
                ).casefold()  # fmt: skip
                return all(t in haystack for t in terms)
            return True

        return [p for p in self.products.values() if keep(p)]

    def lookup(self, product_ids: list[str]) -> tuple[list[StompboxProduct], list[str]]:
        """Return (found products in request order, de-duplicated; missing ids)."""
        found, missing, seen = [], [], set()
        for pid in product_ids:
            if pid in seen:
                continue
            seen.add(pid)
            if pid in self.products:
                found.append(self.products[pid])
            else:
                missing.append(pid)
        return found, missing

    def subcategory_counts(self) -> list[SubcategoryCount]:
        counts = Counter((p.category, p.subcategory) for p in self.products.values())
        return [
            SubcategoryCount(category=c, subcategory=s, product_count=n)
            for (c, s), n in sorted(counts.items())
        ]


# --------------------------------------------------------------------------- helpers


def _clean(value: Any) -> Any:
    """Strip strings; treat empty strings as missing."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _to_date(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    return value


def _to_price(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not a price")
    if isinstance(value, (int, float)):
        # str() avoids binary float artefacts (189.99 -> Decimal("189.99")).
        return Decimal(str(value))
    raise ValueError(f"price must be numeric, got {value!r}")


def _read_sheet(wb, sheet_name: str, required: tuple[str, ...]) -> list[tuple[int, dict]]:
    if sheet_name not in wb.sheetnames:
        raise CatalogueLoadError([f"Sheet {sheet_name!r} not found (have {wb.sheetnames})"])
    rows = wb[sheet_name].iter_rows(values_only=True)
    header = [_clean(h) for h in next(rows, ())]
    missing = [c for c in required if c not in header]
    if missing:
        raise CatalogueLoadError([f"Sheet {sheet_name!r} missing columns: {missing}"])
    out = []
    for excel_row, values in enumerate(rows, start=2):
        record = {h: _clean(v) for h, v in zip(header, values) if h}
        if all(v is None for v in record.values()):
            continue  # trailing blank rows
        out.append((excel_row, record))
    return out


def _fmt(err: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors())


# --------------------------------------------------------------------------- products


def _parse_products(rows: list[tuple[int, dict]]) -> tuple[dict[str, StompboxProduct], list[str]]:
    errors: list[str] = []
    products: dict[str, StompboxProduct] = {}
    seen_urls: dict[str, str] = {}
    for excel_row, r in rows:
        where = f"Products row {excel_row}"
        try:
            prov = {k: r.get(k) for k in PROVENANCE_COLUMNS}
            prov["last_checked"] = _to_date(prov["last_checked"])
            prov["price_snapshot_date"] = _to_date(prov["price_snapshot_date"])
            fields = {k: r.get(k) for k in PRODUCT_COLUMNS if k not in PROVENANCE_COLUMNS}
            fields["displayed_price"] = _to_price(fields["displayed_price"])
            product = StompboxProduct(**fields, provenance=CatalogueProvenance(**prov))
        except ValidationError as e:
            errors.append(f"{where} ({r.get('product_id')}): {_fmt(e)}")
            continue
        except ValueError as e:
            errors.append(f"{where} ({r.get('product_id')}): {e}")
            continue
        if product.product_id in products:
            errors.append(f"{where}: duplicate product_id {product.product_id}")
            continue
        url = str(product.product_url)
        if url in seen_urls:
            errors.append(f"{where}: product_url duplicates {seen_urls[url]}")
            continue
        seen_urls[url] = product.product_id
        products[product.product_id] = product
    return products, errors


# --------------------------------------------------------------------------- competitors


def build_competitors(
    rows: list[tuple[int, dict]],
) -> tuple[list[CompetitorDomain], list[DuplicateCompetitor], list[str]]:
    """Validate competitor rows and deduplicate by normalised hostname (first wins)."""
    errors: list[str] = []
    by_domain: dict[str, CompetitorDomain] = {}
    duplicates: list[DuplicateCompetitor] = []
    for excel_row, r in rows:
        where = f"Competitors row {excel_row} ({r.get('competitor_name')})"
        classification = r.get("classification")
        company_type = CLASSIFICATION_TO_COMPANY_TYPE.get(classification or "")
        if company_type is None:
            errors.append(f"{where}: unmapped classification {classification!r}")
            continue
        try:
            priority = CompetitorPriority(r["priority"]) if r.get("priority") else None
            domain = normalize_hostname(r.get("domain_url") or "")
            competitor = CompetitorDomain(
                competitor_id=r.get("competitor_id"),
                name=r.get("competitor_name"),
                domain=domain,
                homepage_url=r.get("domain_url"),
                company_type=company_type,
                classification=classification,
                priority=priority,
                price_benchmark=priority is not CompetitorPriority.REFERENCE_ONLY,
                approval_status=DomainApprovalStatus.PRECONFIGURED,
                source=CompetitorSource.CATALOGUE_WORKBOOK,
                notes=r.get("notes"),
            )
        except ValidationError as e:
            errors.append(f"{where}: {_fmt(e)}")
            continue
        except ValueError as e:
            errors.append(f"{where}: {e}")
            continue
        if domain in by_domain:
            kept = by_domain[domain]
            duplicates.append(
                DuplicateCompetitor(domain, kept.competitor_id, competitor.competitor_id, competitor.name)
            )
            continue
        by_domain[domain] = competitor
    return list(by_domain.values()), duplicates, errors


# --------------------------------------------------------------------------- entry point


def load_catalogue(
    path: Path,
    products_sheet: str = "Products",
    competitors_sheet: str = "Competitors",
) -> Catalogue:
    if not path.is_file():
        raise CatalogueLoadError([f"Catalogue workbook not found: {path}"])
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        product_rows = _read_sheet(wb, products_sheet, PRODUCT_COLUMNS)
        competitor_rows = _read_sheet(wb, competitors_sheet, COMPETITOR_COLUMNS)
    finally:
        wb.close()

    products, errors = _parse_products(product_rows)
    competitors, duplicates, comp_errors = build_competitors(competitor_rows)
    errors += comp_errors
    if not products:
        errors.append("No products loaded")
    if errors:
        raise CatalogueLoadError(errors)
    return Catalogue(path, products, competitors, duplicates)
