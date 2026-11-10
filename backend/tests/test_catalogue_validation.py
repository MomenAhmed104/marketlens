"""Loader rejects bad data loudly instead of repairing or skipping it."""

from decimal import Decimal

import pytest

from app.services.catalogue import PRODUCT_COLUMNS, CatalogueLoadError, load_catalogue
from tests.conftest import valid_product_row, write_workbook


def test_valid_synthetic_row_loads(tmp_path):
    cat = load_catalogue(write_workbook(tmp_path / "w.xlsx", [valid_product_row()]))
    assert cat.products["SB-901"].displayed_price == Decimal("10.50")
    assert str(cat.products["SB-901"].displayed_price) == "10.50"


def test_missing_workbook(tmp_path):
    with pytest.raises(CatalogueLoadError, match="not found"):
        load_catalogue(tmp_path / "nope.xlsx")


def test_missing_required_column(tmp_path):
    cols = tuple(c for c in PRODUCT_COLUMNS if c != "product_url")
    path = write_workbook(tmp_path / "w.xlsx", [valid_product_row()], product_columns=cols)
    with pytest.raises(CatalogueLoadError, match="missing columns"):
        load_catalogue(path)


def test_duplicate_product_id(tmp_path):
    rows = [valid_product_row(), valid_product_row(product_url="https://example.test/p/2")]
    with pytest.raises(CatalogueLoadError, match="duplicate product_id SB-901"):
        load_catalogue(write_workbook(tmp_path / "w.xlsx", rows))


def test_duplicate_product_url(tmp_path):
    rows = [valid_product_row(), valid_product_row(product_id="SB-902")]
    with pytest.raises(CatalogueLoadError, match="product_url duplicates SB-901"):
        load_catalogue(write_workbook(tmp_path / "w.xlsx", rows))


@pytest.mark.parametrize(
    "override",
    [
        {"product_url": None},
        {"product_url": "not a url"},
        {"product_id": "X-1"},
        {"brand": "  "},
        {"currency": "£"},
        {"displayed_price": "£10"},
        {"displayed_price": -1},
        {"displayed_price": 10.555},
        {"last_checked": "yesterday"},
    ],
)
def test_invalid_rows_rejected(tmp_path, override):
    path = write_workbook(tmp_path / "w.xlsx", [valid_product_row(**override)])
    with pytest.raises(CatalogueLoadError) as exc:
        load_catalogue(path)
    assert "Products row 2" in str(exc.value)


def test_missing_price_is_none_not_zero(tmp_path):
    rows = [
        valid_product_row(displayed_price=None),
        valid_product_row(product_id="SB-902", product_url="https://example.test/p/2", displayed_price=0),
    ]
    cat = load_catalogue(write_workbook(tmp_path / "w.xlsx", rows))
    assert cat.products["SB-901"].displayed_price is None
    assert cat.products["SB-902"].displayed_price == Decimal("0")
