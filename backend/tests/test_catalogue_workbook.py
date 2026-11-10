"""Phase 1 acceptance checks against the real Stompbox workbook."""

from decimal import Decimal

from openpyxl import load_workbook

from app.schemas.catalogue import StompboxProduct


def test_exactly_100_unique_products(catalogue):
    assert len(catalogue.products) == 100
    assert len({p.product_id for p in catalogue.products.values()}) == 100


def test_all_product_urls_populated_and_unique(catalogue):
    urls = [str(p.product_url) for p in catalogue.products.values()]
    assert all(urls)
    assert len(set(urls)) == 100
    assert all(p.product_url.host == "stompbox.co.uk" for p in catalogue.products.values())


def test_every_product_passes_schema_validation(catalogue):
    for p in catalogue.products.values():
        StompboxProduct.model_validate(p.model_dump())


def test_prices_are_exact_decimals_in_gbp(catalogue):
    p = catalogue.products["SB-001"]
    assert p.displayed_price == Decimal("189.99")
    assert p.currency == "GBP"
    assert all(isinstance(x.displayed_price, Decimal) for x in catalogue.products.values())


def test_fields_and_provenance_preserved_verbatim(catalogue):
    p = catalogue.products["SB-001"]
    assert (p.brand, p.manufacturer_model, p.category, p.subcategory) == (
        "Boss", "RC-5", "Effects", "Looper",
    )  # fmt: skip
    assert p.stompbox_sku is None
    assert p.specifications is None
    assert str(p.provenance.evidence_url) == "https://stompbox.co.uk/product-category/effects/loopers/"
    assert p.provenance.last_checked.isoformat() == "2026-11-09"
    # Internal WooCommerce ids stay in stompbox_sku, never in manufacturer_model.
    assert catalogue.products["SB-007"].stompbox_sku == "child-6768"


def test_subcategory_counts_match_workbook_coverage_sheet(catalogue, settings):
    wb = load_workbook(settings.catalogue_path, read_only=True, data_only=True)
    rows = list(wb["Category Coverage"].iter_rows(values_only=True))[1:]
    wb.close()
    expected = {(c, s): n for c, s, n in rows if c}
    actual = {(x.category, x.subcategory): x.product_count for x in catalogue.subcategory_counts()}
    assert actual == expected
