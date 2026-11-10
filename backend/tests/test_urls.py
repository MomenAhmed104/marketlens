import pytest

from app.schemas.search import SearchQuery, SearchScope
from app.services.search.base import RawHit, finalize_results
from app.services.urls import canonical_url


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://www.andertons.co.uk/boss-rc-5-loop-station-pedal/", "https://www.andertons.co.uk/boss-rc-5-loop-station-pedal"),
        ("https://www.andertons.co.uk/boss-rc-5-loop-station-pedal", "https://www.andertons.co.uk/boss-rc-5-loop-station-pedal"),
        ("https://www.andertons.co.uk//x//", "https://www.andertons.co.uk//x"),
        ("https://www.andertons.co.uk/", "https://www.andertons.co.uk/"),
        ("https://www.andertons.co.uk", "https://www.andertons.co.uk/"),
        ("HTTPS://WWW.Thomann.co.uk:443/boss_rc_5.htm#reviews", "https://www.thomann.co.uk/boss_rc_5.htm"),
        ("http://example.co.uk:8080/a/", "http://example.co.uk:8080/a"),
        ("https://shop.example.com/p/?colour=red", "https://shop.example.com/p?colour=red"),
        # Gear4music locale storefronts -> UK storefront
        ("https://www.gear4music.com/us/en/Guitar-and-Bass/Boss-RC-5-Loop-Station-Guitar-Loop-Pedal/3LJT",
         "https://www.gear4music.com/Guitar-and-Bass/Boss-RC-5-Loop-Station-Guitar-Loop-Pedal/3LJT"),
        ("https://www.gear4music.com/ie/en/G4M/Boss-RC-5-Secondhand/70E5/", "https://www.gear4music.com/G4M/Boss-RC-5-Secondhand/70E5"),
        ("https://www.gear4music.com/us/en/", "https://www.gear4music.com/"),
        ("https://www.gear4music.com/Guitar-and-Bass/X/3LJT", "https://www.gear4music.com/Guitar-and-Bass/X/3LJT"),
        # Path segments that merely look like a locale are left alone elsewhere,
        # and Gear4music paths that are not a two-letter prefix are untouched.
        ("https://www.andertons.co.uk/us/en/thing", "https://www.andertons.co.uk/us/en/thing"),
        ("https://www.gear4music.com/usa/eng/thing", "https://www.gear4music.com/usa/eng/thing"),
    ],
)  # fmt: skip
def test_canonical_url(raw, expected):
    assert canonical_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "/relative/path", "andertons.co.uk/x"])
def test_canonical_url_rejects_non_absolute(raw):
    with pytest.raises(ValueError):
        canonical_url(raw)


def test_finalize_dedupes_on_canonical_and_keeps_original():
    q = SearchQuery(text="x", scope=SearchScope.DOMAIN, domains=("gear4music.com", "andertons.co.uk"))
    hits = [
        RawHit(url="https://www.gear4music.com/us/en/Guitar-and-Bass/RC5/3LJT"),
        RawHit(url="https://www.gear4music.com/Guitar-and-Bass/RC5/3LJT/"),  # same page, UK form
        RawHit(url="https://www.andertons.co.uk/boss-rc-5/"),
        RawHit(url="https://www.andertons.co.uk/boss-rc-5"),
    ]
    results, _ = finalize_results("p", q, hits)
    assert [(str(r.url), r.canonical_url) for r in results] == [
        ("https://www.gear4music.com/us/en/Guitar-and-Bass/RC5/3LJT",
         "https://www.gear4music.com/Guitar-and-Bass/RC5/3LJT"),
        ("https://www.andertons.co.uk/boss-rc-5/", "https://www.andertons.co.uk/boss-rc-5"),
    ]  # fmt: skip
