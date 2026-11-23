"""Product images: extraction priority, validation, failure isolation, catalogue sidecar, proxy.

No network: page and image fetches go through httpx.MockTransport with public IP-literal hosts,
so no DNS lookup or outbound request is ever made.
"""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.images import ImageSource, ImageStatus, ProductImage
from app.services.images import ImageCache, ImageFinder
from app.services.images.catalogue import collect, load_catalogue_images, write_catalogue_images
from app.services.images.extract import find_primary_image, usable_image_url
from app.services.images.net import UnsafeURL, check_public_url
from app.services.matching import evaluate_product
from tests.search_fakes import make_settings
from tests.test_intelligence_agent import AND, BECK, GIB, cands, make, run

PAGE = "http://93.184.216.34/products/item-1"  # public IP literal: no DNS, no real request (mocked)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200


def html(*parts: str) -> str:
    return "<html><head>" + "".join(parts) + "</head><body></body></html>"


def ld(obj) -> str:
    return f'<script type="application/ld+json">{json.dumps(obj)}</script>'


# --------------------------------------------------------------------------- extraction priority


def test_json_ld_product_image_wins_over_open_graph():
    page = html('<meta property="og:image" content="/og.jpg">',
                ld({"@context": "https://schema.org", "@type": "Product", "name": "X", "image": ["/a.jpg", "/b.jpg"]}))
    f = find_primary_image(page, PAGE)
    assert (f.url, f.source) == ("http://93.184.216.34/a.jpg", ImageSource.JSON_LD)
    assert f.additional == ["http://93.184.216.34/b.jpg"]


@pytest.mark.parametrize("node", [
    {"@graph": [{"@type": "WebPage"}, {"@type": "Product", "image": {"@type": "ImageObject", "url": "https://cdn.example.com/p.webp"}}]},
    [{"@type": "BreadcrumbList"}, {"@type": ["Product", "Thing"], "image": "https://cdn.example.com/p.webp"}],
    {"@type": "ProductGroup", "hasVariant": [{"@type": "Product", "image": "https://cdn.example.com/p.webp"}]},
    {"@type": "https://schema.org/Product", "image": {"contentUrl": "https://cdn.example.com/p.webp"}},
])  # fmt: skip
def test_json_ld_shapes(node):
    assert find_primary_image(html(ld(node)), PAGE).url == "https://cdn.example.com/p.webp"


def test_open_graph_then_product_meta_then_gallery():
    og = html('<meta property="og:image:secure_url" content="https://cdn.example.com/og.jpg">')
    assert find_primary_image(og, PAGE).source is ImageSource.OPEN_GRAPH
    meta = html('<meta name="twitter:image" content="https://cdn.example.com/tw.jpg">')
    assert find_primary_image(meta, PAGE).source is ImageSource.PRODUCT_META
    gallery = '<html><body><img class="site-logo" src="/logo.png"><img class="product-gallery__main" src="/g1.jpg"></body></html>'
    f = find_primary_image(gallery, PAGE)
    assert (f.url, f.source) == ("http://93.184.216.34/g1.jpg", ImageSource.PAGE_IMAGE)


def test_logos_icons_svg_data_and_bad_json_are_skipped():
    page = html('<meta property="og:image" content="/static/logo.png">',
                '<script type="application/ld+json">{not json</script>',
                ld({"@type": "Product", "image": ["data:image/png;base64,AAAA", "/icons/cart.svg", "/img/product-1.jpg"]}))
    assert find_primary_image(page, PAGE).url == "http://93.184.216.34/img/product-1.jpg"
    assert find_primary_image(html('<meta property="og:image" content="/favicon.ico">'), PAGE) is None
    assert find_primary_image("<html>no images here</html>", PAGE) is None
    assert find_primary_image("<<<not html", PAGE) is None


@pytest.mark.parametrize("raw, expected", [
    ("//cdn.example.com/a.jpg", "http://cdn.example.com/a.jpg"),
    ("a.jpg", "http://93.184.216.34/products/a.jpg"),
    ("ftp://x/a.jpg", None), ("javascript:alert(1)", None), ("", None), (None, None),
])  # fmt: skip
def test_urls_are_only_resolved_never_invented(raw, expected):
    assert usable_image_url(raw, PAGE) == expected


@pytest.mark.parametrize("industry_page", [
    ld({"@type": "Product", "name": "Linen shirt", "image": "https://shop.example.com/shirt.jpg"}),  # fashion
    ld({"@type": "Product", "name": "4K TV 55in", "image": "https://shop.example.com/tv.jpg"}),  # electronics
    '<meta property="og:image" content="https://shop.example.com/kettle.jpg">',  # home appliances
])  # fmt: skip
def test_industry_neutral(industry_page):
    assert find_primary_image(html(industry_page), PAGE) is not None


# --------------------------------------------------------------------------- safety


@pytest.mark.parametrize("url", ["http://127.0.0.1/a.png", "http://10.0.0.5/a.png", "http://169.254.169.254/latest",
                                 "http://[::1]/a.png", "file:///etc/passwd", "http://localhost/a.png"])
def test_private_and_non_http_addresses_are_refused(url):
    with pytest.raises(UnsafeURL):
        asyncio.run(check_public_url(url))


# --------------------------------------------------------------------------- finder (mocked HTTP)


def finder_with(handler, tmp_path):
    s = make_settings(product_images_enabled=True, image_cache_dir=tmp_path / "cache")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ImageFinder(s, cache=ImageCache(s.image_cache_dir), client=client), s


def find(f, url=PAGE) -> ProductImage:
    try:
        return asyncio.run(f.find(url))
    finally:
        asyncio.run(f.aclose())


def test_found_image_is_validated_and_cached(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/products/item-1":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text=html('<meta property="og:image" content="/p.png">'))
        return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)

    f, s = finder_with(handler, tmp_path)
    img = find(f)
    assert img.status is ImageStatus.FOUND and img.url == "http://93.184.216.34/p.png"
    assert img.source is ImageSource.OPEN_GRAPH and img.source_page_url == PAGE and img.retrieved_at
    assert ImageCache(s.image_cache_dir).get(img.url) == (PNG, "image/png")


def test_falls_back_to_the_next_listing_image_and_reports_an_invalid_one(tmp_path):
    page = html(ld({"@type": "Product", "image": ["/broken.jpg", "/ok.jpg"]}))

    def handler(req):
        if req.url.path.endswith("item-1"):
            return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=page)
        if req.url.path == "/broken.jpg":
            return httpx.Response(200, headers={"content-type": "text/html"}, text="not an image")
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=PNG)

    f, _ = finder_with(handler, tmp_path)
    img = find(f)
    assert img.status is ImageStatus.FOUND and img.url.endswith("/ok.jpg") and img.note

    def all_broken(req):
        if req.url.path.endswith("item-1"):
            return httpx.Response(200, headers={"content-type": "text/html"}, text=page)
        return httpx.Response(404)

    f, _ = finder_with(all_broken, tmp_path)
    img = find(f)
    assert img.status is ImageStatus.INVALID and img.url.endswith("/broken.jpg")  # kept for traceability
    assert not img.usable


@pytest.mark.parametrize("response, status", [
    (httpx.Response(403), ImageStatus.BLOCKED),
    (httpx.Response(429), ImageStatus.BLOCKED),
    (httpx.Response(500), ImageStatus.FETCH_FAILED),
    (httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF"), ImageStatus.FETCH_FAILED),
    (httpx.Response(200, headers={"content-type": "text/html"}, text="<title>Just a moment...</title>"), ImageStatus.BLOCKED),
    (httpx.Response(200, headers={"content-type": "text/html"}, text=html("<title>Plain page</title>")), ImageStatus.NOT_FOUND),
])  # fmt: skip
def test_page_failures_become_statuses(response, status, tmp_path):
    f, _ = finder_with(lambda req: response, tmp_path)
    img = find(f)
    assert img.status is status and img.url is None and img.note


def test_network_errors_and_private_redirects_never_raise(tmp_path):
    def boom(req):
        raise httpx.ConnectTimeout("timeout")

    f, _ = finder_with(boom, tmp_path)
    assert find(f).status is ImageStatus.FETCH_FAILED

    f, _ = finder_with(lambda req: httpx.Response(302, headers={"location": "http://127.0.0.1/admin"}), tmp_path)
    img = find(f)
    assert img.status is ImageStatus.FETCH_FAILED and "not allowed" in img.note


# --------------------------------------------------------------------------- agent integration


class FakeImages:
    def __init__(self, fail_for=()):
        self.fail_for, self.asked = set(fail_for), []

    async def find(self, page_url):
        self.asked.append(page_url)
        if page_url in self.fail_for:
            raise RuntimeError("boom")
        return ProductImage(status=ImageStatus.FOUND, url=f"{page_url}/image.jpg", source=ImageSource.OPEN_GRAPH,
                            source_page_url=page_url)  # fmt: skip

    async def aclose(self):
        pass


def test_each_listing_gets_its_own_image_and_failures_never_break_extraction(catalogue):
    images = FakeImages(fail_for={AND})
    agent = make(catalogue)
    agent.images = images
    result = run(agent, catalogue)
    c = cands(result)
    assert c[AND].image.status is ImageStatus.NOT_ATTEMPTED and "RuntimeError" in c[AND].image.note
    assert c[AND].extraction_status.value == "COMPLETED" and c[AND].price == Decimal("189.00")  # unaffected
    assert c[BECK].image.url == f"{BECK}/image.jpg"  # its own listing's image, never the catalogue one
    assert c[GIB].image.status is ImageStatus.NOT_ATTEMPTED and c[GIB].image.note == "The page was not retrieved"
    assert set(images.asked) == {u for u, x in c.items() if x.page.status and x.page.status.value == "OK"}


def test_without_a_finder_images_are_not_attempted(catalogue):
    result = run(make(catalogue), catalogue)
    assert {x.image.status for x in cands(result).values()} == {ImageStatus.NOT_ATTEMPTED}


def test_images_never_change_matching_or_prices(catalogue):
    with_images = make(catalogue)
    with_images.images = FakeImages()
    a = run(with_images, catalogue).products[0]
    b = run(make(catalogue), catalogue).products[0]
    p = catalogue.products["SB-001"]
    now = datetime(2026, 11, 12, 12, tzinfo=UTC)
    sa = evaluate_product(a, p, 24, now)
    sb = evaluate_product(b, p, 24, now)
    strip = lambda s: [(m.product_url, m.match_status, m.price.status, m.price.price, m.comparison.eligible)  # noqa: E731
                       for m in s.candidates]  # fmt: skip
    assert strip(sa) == strip(sb)


def test_records_without_an_image_still_load(catalogue):
    from app.agents.intelligence.schemas import CompetitorProduct

    c = cands(run(make(catalogue), catalogue))[AND]
    raw = c.model_dump(mode="json")
    raw.pop("image")
    assert CompetitorProduct.model_validate(raw).image is None


# --------------------------------------------------------------------------- catalogue sidecar and API


def test_catalogue_sidecar_round_trip_and_startup_merge(tmp_path):
    path = tmp_path / "product_images.json"
    assert load_catalogue_images(path) == {}
    img = ProductImage(status=ImageStatus.FOUND, url="https://cdn.example.com/rc5.jpg", source=ImageSource.JSON_LD,
                       source_page_url="https://stompbox.example/rc5")  # fmt: skip
    write_catalogue_images(path, {"SB-001": img, "SB-999": img})
    assert load_catalogue_images(path)["SB-001"] == img
    with TestClient(create_app(make_settings(catalogue_images_path=path))) as client:
        p = client.get("/api/catalogue/products/SB-001").json()
        other = client.get("/api/catalogue/products/SB-002").json()
    assert p["image"]["url"] == "https://cdn.example.com/rc5.jpg" and p["image"]["status"] == "FOUND"
    assert other["image"] is None  # no entry: the dashboard shows a placeholder


def test_a_broken_sidecar_never_stops_startup(tmp_path):
    path = tmp_path / "product_images.json"
    path.write_text("{broken", encoding="utf-8")
    assert load_catalogue_images(path) == {}
    path.write_text(json.dumps({"images": {"SB-001": {"status": "NOPE"}}}), encoding="utf-8")
    assert load_catalogue_images(path) == {}
    with TestClient(create_app(make_settings(catalogue_images_path=path))) as client:
        assert client.get("/api/health").status_code == 200


def test_collect_uses_each_products_own_page_and_keeps_found_entries(tmp_path):
    s = make_settings(catalogue_images_path=tmp_path / "product_images.json")

    F = FakeImages
    f = F()
    images = asyncio.run(collect(s, ["SB-001", "SB-002"], refresh=False, finder=f)).images
    assert set(images) == {"SB-001", "SB-002"} and all(i.usable for i in images.values())
    from app.services.catalogue import load_catalogue

    cat = load_catalogue(s.catalogue_path, s.catalogue_products_sheet, s.catalogue_competitors_sheet)
    assert sorted(f.asked) == sorted(str(cat.products[i].product_url) for i in ("SB-001", "SB-002"))  # own pages
    write_catalogue_images(s.catalogue_images_path, images)
    f2 = F()
    asyncio.run(collect(s, ["SB-001", "SB-002"], refresh=False, finder=f2))
    assert f2.asked == []  # already found: not fetched again


def test_image_proxy_serves_cache_and_refuses_private_addresses(tmp_path):
    s = make_settings(image_cache_dir=tmp_path / "cache")
    ImageCache(s.image_cache_dir).put("https://cdn.example.com/p.png", PNG, "image/png")
    with TestClient(create_app(s)) as client:
        r = client.get("/api/images", params={"url": "https://cdn.example.com/p.png"})
        assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-image-cache"] == "hit"
        assert client.get("/api/images", params={"url": "http://127.0.0.1/secret.png"}).status_code == 400
        assert client.get("/api/images", params={"url": "file:///etc/passwd"}).status_code == 400
