"""Stage 9.2 fix: country-code second-level suffixes ("x.com.ua" was recorded as "com.ua").

Run B grouped four Ukrainian shops (evomusic, musicshop, muzmarket, soundsgood .com.ua) into one
candidate "com.ua". The registrable domain now keeps a generic second-level label under a
two-letter country-code TLD as part of the suffix."""

import pytest

from app.services.domains import canonical_domain, is_public_suffix, registrable_domain


@pytest.mark.parametrize("host, expected", [
    ("https://www.evomusic.com.ua/catalog/tuner", "evomusic.com.ua"),
    ("shop.musicshop.com.ua", "musicshop.com.ua"),
    ("andertons.co.uk", "andertons.co.uk"),
    ("blog.andertons.co.uk", "andertons.co.uk"),
    ("www.charity.org.uk", "charity.org.uk"),
    ("schoeler-pianohaus.de", "schoeler-pianohaus.de"),
    ("shop.schoeler-pianohaus.de", "schoeler-pianohaus.de"),
    ("www.gear4music.com", "gear4music.com"),
    ("a.b.example.com", "example.com"),
    ("store.co.jp", "store.co.jp"),
    ("music.com.au", "music.com.au"),
    ("thomann.co.uk", "thomann.co.uk"),
])  # fmt: skip
def test_registrable_domain(host, expected):
    assert registrable_domain(host) == expected


def test_different_shops_under_one_country_suffix_stay_separate():
    hosts = ["evomusic.com.ua", "musicshop.com.ua", "muzmarket.com.ua", "soundsgood.com.ua"]
    assert len({registrable_domain(h) for h in hosts}) == 4


@pytest.mark.parametrize("value, suffix", [
    ("com.ua", True), ("co.uk", True), ("org.uk", True), ("com.au", True),
    ("example.de", False), ("andertons.co.uk", False), ("gibson.com", False), ("de", False),
])  # fmt: skip
def test_public_suffixes(value, suffix):
    assert is_public_suffix(value) is suffix


def test_canonical_domain_refuses_a_public_suffix_and_subdomains():
    with pytest.raises(ValueError):
        canonical_domain("com.ua")
    with pytest.raises(ValueError):
        canonical_domain("shop.evomusic.com.ua")
    assert canonical_domain("WWW.EvoMusic.com.ua") == "evomusic.com.ua"


def test_configured_competitors_are_unchanged(catalogue):
    # The 13 configured domains must keep their registrable domain (no duplicate or renamed records).
    assert {registrable_domain(c.domain) for c in catalogue.competitors} == {
        "andertons.co.uk", "guitarguitar.co.uk", "langleyguitarcentre.co.uk", "themusicdept.com", "gear4music.com",
        "thomann.co.uk", "bax-shop.co.uk", "projectmusic.co.uk", "the58sound.com", "perfectpitch.co.uk",
        "southlondonmusicshop.co.uk", "yamahamusiclondon.com", "gibson.com"}  # fmt: skip


# --------------------------------------------------------------------------- Public Suffix List library


def test_the_bundled_public_suffix_list_is_used_offline():
    from app.services import domains

    domains._psl.cache_clear()
    psl = domains._psl()  # loaded under the suite's network guard: the bundled copy, no download
    assert psl is not None and psl.publicsuffix("x.com.ua") == "com.ua"


@pytest.mark.parametrize("host, expected", [
    ("user.github.io", "user.github.io"),  # private suffix: each user site is its own domain
    ("other.github.io", "other.github.io"),
    ("shop.blogspot.com", "shop.blogspot.com"),
    ("www.example.com", "example.com"),
])  # fmt: skip
def test_private_suffixes(host, expected):
    assert registrable_domain(host) == expected


def test_private_suffix_itself_is_public():
    assert is_public_suffix("github.io") and not is_public_suffix("user.github.io")


def test_the_previous_rule_is_the_fallback(monkeypatch):
    from app.services import domains

    monkeypatch.setattr(domains, "_psl", lambda: None)
    assert registrable_domain("shop.evomusic.com.ua") == "evomusic.com.ua"
    assert registrable_domain("blog.andertons.co.uk") == "andertons.co.uk"
    assert is_public_suffix("com.ua") and not is_public_suffix("andertons.co.uk")
