from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.core.config import Settings
from app.main import create_app
from app.services.catalogue import COMPETITOR_COLUMNS, PRODUCT_COLUMNS, load_catalogue


@pytest.fixture(scope="session")
def settings(tmp_path_factory) -> Settings:
    # Real catalogue, but never the real review database.
    return Settings(review_db_path=tmp_path_factory.mktemp("review") / "review.sqlite3")


@pytest.fixture(scope="session")
def catalogue(settings):
    return load_catalogue(settings.catalogue_path)


@pytest.fixture(scope="session")
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def valid_product_row(**overrides) -> dict:
    """A synthetic, obviously-fake row used only to exercise loader validation."""
    row = {
        "product_id": "SB-901",
        "product_name": "Test Pedal",
        "brand": "TestBrand",
        "manufacturer_model": "TP-1",
        "category": "Effects",
        "subcategory": "Test",
        "displayed_price": 10.5,
        "currency": "GBP",
        "product_url": "https://example.test/p/1",
        "verification_status": "test",
        "evidence_url": "https://example.test/c",
        "last_checked": "2026-11-09",
        "source_type": "test",
        "price_snapshot_date": "2026-11-09",
    }
    row.update(overrides)
    return row


def valid_competitor_row(**overrides) -> dict:
    row = {
        "competitor_id": "C-901",
        "competitor_name": "Test Shop",
        "domain_url": "https://www.example.test/",
        "priority": "High",
        "classification": "Primary retailer",
        "notes": None,
    }
    row.update(overrides)
    return row


def write_workbook(
    path: Path,
    products: list[dict],
    competitors: list[dict] | None = None,
    product_columns=PRODUCT_COLUMNS,
) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Products"
    ws.append(list(product_columns))
    for p in products:
        ws.append([p.get(c) for c in product_columns])
    cs = wb.create_sheet("Competitors")
    cs.append(list(COMPETITOR_COLUMNS))
    for c in competitors if competitors is not None else [valid_competitor_row()]:
        cs.append([c.get(col) for col in COMPETITOR_COLUMNS])
    wb.save(path)
    return path
