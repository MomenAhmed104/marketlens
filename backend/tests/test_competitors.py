import pytest

from app.schemas.common import DomainApprovalStatus
from app.schemas.competitor import CompanyType
from app.services.catalogue import CatalogueLoadError, build_competitors, load_catalogue
from app.services.domains import normalize_hostname
from tests.conftest import valid_competitor_row, valid_product_row, write_workbook

EXPECTED_DOMAINS = {
    "andertons.co.uk", "guitarguitar.co.uk", "langleyguitarcentre.co.uk",
    "themusicdept.com", "gear4music.com", "thomann.co.uk", "bax-shop.co.uk",
    "projectmusic.co.uk", "the58sound.com", "perfectpitch.co.uk",
    "southlondonmusicshop.co.uk", "yamahamusiclondon.com", "gibson.com",
}  # fmt: skip


def test_workbook_competitors_match_baseline(catalogue):
    domains = [c.domain for c in catalogue.competitors]
    assert len(domains) == 13
    assert set(domains) == EXPECTED_DOMAINS
    assert catalogue.duplicate_competitors == []
    assert "pmt" not in " ".join(domains)
    assert all(c.approval_status is DomainApprovalStatus.PRECONFIGURED for c in catalogue.competitors)


def test_gibson_is_manufacturer_reference_not_benchmark(catalogue):
    gibson = next(c for c in catalogue.competitors if c.domain == "gibson.com")
    assert gibson.company_type is CompanyType.MANUFACTURER
    assert gibson.price_benchmark is False
    assert gibson.classification == "Manufacturer"
    others = [c for c in catalogue.competitors if c.domain != "gibson.com"]
    assert all(c.price_benchmark for c in others)


def test_dedup_by_normalised_hostname_first_wins():
    rows = [
        (2, valid_competitor_row(competitor_id="C-1", domain_url="https://www.Andertons.co.uk/")),
        (3, valid_competitor_row(competitor_id="C-2", competitor_name="Andertons dup",
                                 domain_url="http://andertons.co.uk/some/path")),
    ]  # fmt: skip
    competitors, dups, errors = build_competitors(rows)
    assert errors == []
    assert [c.competitor_id for c in competitors] == ["C-1"]
    assert dups[0].domain == "andertons.co.uk" and dups[0].dropped_id == "C-2"


def test_unmapped_classification_fails(tmp_path):
    path = write_workbook(
        tmp_path / "w.xlsx", [valid_product_row()], [valid_competitor_row(classification="Mystery")]
    )
    with pytest.raises(CatalogueLoadError, match="unmapped classification"):
        load_catalogue(path)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://www.andertons.co.uk/", "andertons.co.uk"),
        ("HTTP://WWW.Thomann.co.uk:443/path?q=1", "thomann.co.uk"),
        ("bax-shop.co.uk", "bax-shop.co.uk"),
        ("shop.gibson.com", "shop.gibson.com"),
        ("https://gear4music.com.", "gear4music.com"),
    ],
)
def test_normalize_hostname(raw, expected):
    assert normalize_hostname(raw) == expected


@pytest.mark.parametrize("raw", ["", "localhost", "https:///nohost"])
def test_normalize_hostname_rejects(raw):
    with pytest.raises(ValueError):
        normalize_hostname(raw)
