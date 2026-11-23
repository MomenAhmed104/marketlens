"""Product image fixes from the review: lazy-loaded / responsive images and <base href> (I5), and
one total deadline per image lookup and per /api/images cache miss (C1).

No network: httpx.MockTransport with public IP-literal hosts (no DNS, no real request).
"""

import asyncio
import time
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.images import get_client_factory, get_url_allowed
from app.main import create_app
from app.schemas.images import ImageSource, ImageStatus
from app.services.images import ImageCache, ImageFinder
from app.services.images.extract import find_primary_image, parse_srcset
from tests.search_fakes import make_settings
from tests.test_intelligence_agent import AND, BECK, cands, make, run

PAGE = "http://93.184.216.34/products/item-1"
HOST = "http://93.184.216.34"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200


def gallery(attrs: str) -> str:
    return f'<html><body><img class="product-gallery__image" {attrs}></body></html>'


# --------------------------------------------------------------------------- I5: lazy-loaded images


@pytest.mark.parametrize("src", [
    'src="data:image/gif;base64,R0lGODlhAQABAAAAACw="',
    'src="/assets/blank.gif"',
    'src="/img/spacer.png"',
    'src="/img/placeholder.jpg"',
    "",  # no src at all
])  # fmt: skip
@pytest.mark.parametrize("lazy", ["data-src", "data-lazy-src", "data-lazy", "data-original"])
def test_placeholder_src_never_hides_the_lazy_loaded_image(src, lazy):
    f = find_primary_image(gallery(f'{src} {lazy}="/media/product-123.jpg"'), PAGE)
    assert f is not None and f.url == f"{HOST}/media/product-123.jpg" and f.source is ImageSource.PAGE_IMAGE


def test_zoom_image_is_preferred_and_a_real_src_still_works_alone():
    f = find_primary_image(gallery('src="/m/p-small.jpg" data-src="/m/p.jpg" data-zoom-image="/m/p-zoom.jpg"'), PAGE)
    assert f.url == f"{HOST}/m/p-zoom.jpg"
    assert find_primary_image(gallery('src="/m/p.jpg"'), PAGE).url == f"{HOST}/m/p.jpg"


def test_unsafe_lazy_values_are_skipped_for_the_next_alternative():
    f = find_primary_image(gallery('data-src="javascript:alert(1)" src="/m/real.jpg"'), PAGE)
    assert f.url == f"{HOST}/m/real.jpg"
    assert find_primary_image(gallery('data-src="data:image/png;base64,AAAA" src="/logo.png"'), PAGE) is None


def test_one_img_contributes_one_url_never_all_of_its_sizes():
    html = ('<html><body><img class="product-main" srcset="/a-400.jpg 400w, /a-1200.jpg 1200w" src="/a-400.jpg">'
            '<img class="product-thumb" src="/b.jpg"></body></html>')
    f = find_primary_image(html, PAGE)
    assert f.url == f"{HOST}/a-1200.jpg" and f.additional == [f"{HOST}/b.jpg"]


# --------------------------------------------------------------------------- I5: srcset


@pytest.mark.parametrize("value, best", [
    ("/a-400.jpg 400w, /a-1200.jpg 1200w, /a-800.jpg 800w", "/a-1200.jpg"),
    ("/a.jpg 1x, /a@2x.jpg 2x", "/a@2x.jpg"),
    ("/a.jpg 2x, /b.jpg 300w", "/b.jpg"),  # a width outranks a density
    ("/only.jpg", "/only.jpg"),
    ("/a.jpg, /b.jpg 2x", "/b.jpg"),
    ("https://img.example.com/c_fill,w_400/p.jpg 400w, https://img.example.com/c_fill,w_900/p.jpg 900w",
     "https://img.example.com/c_fill,w_900/p.jpg"),  # commas inside URLs
])  # fmt: skip
def test_parse_srcset_picks_the_best_candidate(value, best):
    assert parse_srcset(value)[0] == best


@pytest.mark.parametrize("value", ["", None, " , ,", "   "])
def test_parse_srcset_handles_empty_input(value):
    assert parse_srcset(value) == []


def test_srcset_and_data_srcset_on_gallery_images():
    assert find_primary_image(gallery('srcset="/s-300.jpg 300w, /s-900.jpg 900w"'), PAGE).url == f"{HOST}/s-900.jpg"
    f = find_primary_image(gallery('src="data:," data-srcset="/d-1x.jpg 1x, /d-2x.jpg 2x"'), PAGE)
    assert f.url == f"{HOST}/d-2x.jpg"


# --------------------------------------------------------------------------- I5: <base href>


def test_base_href_resolves_relative_image_urls():
    html = '<html><head><base href="https://cdn.example.com/store/"><meta property="og:image" content="img/x.jpg"></head></html>'
    assert find_primary_image(html, PAGE).url == "https://cdn.example.com/store/img/x.jpg"


def test_relative_base_href_resolves_against_the_page_and_unsafe_bases_are_ignored():
    rel = '<html><head><base href="/shop/"><meta property="og:image" content="img/x.jpg"></head></html>'
    assert find_primary_image(rel, PAGE).url == f"{HOST}/shop/img/x.jpg"
    bad = '<html><head><base href="javascript:void(0)"><meta property="og:image" content="img/x.jpg"></head></html>'
    assert find_primary_image(bad, PAGE).url == f"{HOST}/products/img/x.jpg"
    first = ('<html><head><base href="https://a.example.com/"><base href="https://b.example.com/">'
             '<meta property="og:image" content="x.jpg"></head></html>')
    assert find_primary_image(first, PAGE).url == "https://a.example.com/x.jpg"  # the first <base> counts


# --------------------------------------------------------------------------- I5: priority unchanged


def test_lazy_and_srcset_gallery_images_never_outrank_higher_priority_sources():
    lazy = '<img class="product-main" src="data:," data-src="/gallery.jpg" srcset="/g-2000.jpg 2000w">'
    og = '<meta property="og:image" content="/og.jpg">'
    ld = '<script type="application/ld+json">{"@type": "Product", "image": "/ld.jpg"}</script>'
    assert find_primary_image(f"<html><head>{og}</head><body>{lazy}</body></html>", PAGE).source is ImageSource.OPEN_GRAPH
    f = find_primary_image(f"<html><head>{og}{ld}</head><body>{lazy}</body></html>", PAGE)
    assert (f.url, f.source) == (f"{HOST}/ld.jpg", ImageSource.JSON_LD)
    assert f"{HOST}/g-2000.jpg" in f.additional  # still recorded as an additional listing image


def test_itemprop_image_on_an_img_keeps_the_metadata_level_with_lazy_attributes():
    html = ('<html><body><img itemprop="image" src="data:," data-src="/meta.jpg">'
            '<img class="product-main" src="/gallery.jpg"></body></html>')
    f = find_primary_image(html, PAGE)
    assert (f.url, f.source) == (f"{HOST}/meta.jpg", ImageSource.PRODUCT_META)


# --------------------------------------------------------------------------- C1: total deadline


def html_page(body: str = '<meta property="og:image" content="/p.png">') -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html"}, text=f"<html><head>{body}</head></html>")


def finder(handler, tmp_path, deadline=0.3, timeout=5):
    s = make_settings(product_images_enabled=True, image_cache_dir=tmp_path / "cache",
                      image_lookup_deadline_seconds=deadline, image_fetch_timeout_seconds=timeout)  # fmt: skip
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ImageFinder(s, cache=ImageCache(s.image_cache_dir), client=client), client


def timed_find(f, url=PAGE):
    async def go():
        start = time.monotonic()
        try:
            return await f.find(url), time.monotonic() - start
        finally:
            await f.aclose()

    return asyncio.run(go())


def test_a_slow_page_response_exceeds_the_total_deadline(tmp_path):
    async def slow(request):
        await asyncio.sleep(5)
        return html_page()

    f, _ = finder(slow, tmp_path)
    img, elapsed = timed_find(f)
    assert img.status is ImageStatus.FETCH_FAILED and "deadline" in img.note and img.url is None
    assert elapsed < 2


def test_a_slow_dripping_body_cannot_reset_the_deadline_per_read(tmp_path):
    async def drip():
        for _ in range(100):
            await asyncio.sleep(0.05)  # each read is far below the per-read timeout
            yield b"<!-- x -->"

    f, _ = finder(lambda r: httpx.Response(200, headers={"content-type": "text/html"}, content=drip()), tmp_path)
    img, elapsed = timed_find(f)
    assert img.status is ImageStatus.FETCH_FAILED and "deadline" in img.note
    assert elapsed < 2  # 100 x 0.05 s would take 5 s without the total deadline


def test_redirects_cannot_extend_the_deadline(tmp_path):
    hops = []

    async def redirecting(request):
        hops.append(str(request.url))
        await asyncio.sleep(0.12)  # each hop alone is well under the deadline
        return httpx.Response(302, headers={"location": f"/hop-{len(hops)}"})

    f, _ = finder(redirecting, tmp_path, deadline=0.3)
    img, elapsed = timed_find(f)
    assert img.status is ImageStatus.FETCH_FAILED and "deadline" in img.note
    assert len(hops) < 5 and elapsed < 2  # stopped part-way through the redirect chain


def test_slow_image_candidates_share_the_same_deadline(tmp_path):
    page = ('<html><head><script type="application/ld+json">{"@type": "Product", "image": '
            '["/1.jpg", "/2.jpg", "/3.jpg", "/4.jpg"]}</script></head></html>')

    async def handler(request):
        if request.url.path == "/products/item-1":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=page)
        await asyncio.sleep(0.15)
        return httpx.Response(404)

    f, _ = finder(handler, tmp_path, deadline=0.3)
    img, elapsed = timed_find(f)
    assert img.status is ImageStatus.FETCH_FAILED and "deadline" in img.note and elapsed < 2


def test_a_lookup_within_the_deadline_is_unaffected(tmp_path):
    def handler(request):
        if request.url.path == "/products/item-1":
            return html_page()
        return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)

    f, _ = finder(handler, tmp_path, deadline=5)
    img, _ = timed_find(f)
    assert img.status is ImageStatus.FOUND


def test_the_http_client_is_closed_after_a_deadline_expires(tmp_path):
    async def slow(request):
        await asyncio.sleep(5)
        return html_page()

    f, client = finder(slow, tmp_path)
    timed_find(f)
    assert client.is_closed


def test_a_timed_out_image_never_fails_product_extraction(catalogue, tmp_path, monkeypatch):
    # The fake candidate pages use real retailer hostnames: skip the public-address DNS check so
    # the test never resolves them (the HTTP itself is mocked).
    from app.services.images import net

    async def no_dns(url):
        return None

    monkeypatch.setattr(net, "check_public_url", no_dns)

    async def slow(request):
        await asyncio.sleep(5)
        return html_page()

    f, _ = finder(slow, tmp_path, deadline=0.2)
    agent = make(catalogue)
    agent.images = f
    start = time.monotonic()
    result = run(agent, catalogue)
    c = cands(result)
    assert time.monotonic() - start < 5
    assert c[AND].extraction_status.value == "COMPLETED" and c[AND].price == Decimal("189.00")
    # Every page ends exactly as it does without images (BECK's fake LLM 2 output fails by design).
    baseline = cands(run(make(catalogue), catalogue))
    outcome = {u: (x.extraction_status, x.price) for u, x in c.items()}
    assert outcome == {u: (x.extraction_status, x.price) for u, x in baseline.items()}
    assert c[BECK].extraction_status.value == "FAILED"
    retrieved = [x for x in c.values() if x.page.status and x.page.status.value == "OK"]
    assert retrieved and all(x.image.status is ImageStatus.FETCH_FAILED and "deadline" in x.image.note for x in retrieved)
    assert not any("image" in w.lower() for w in result.warnings)  # nothing escalated to the run


def test_the_image_endpoint_respects_the_deadline(tmp_path):
    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)

    s = make_settings(image_cache_dir=tmp_path / "cache", image_lookup_deadline_seconds=0.3)
    app = create_app(s)
    clients: list[httpx.AsyncClient] = []

    def factory():
        c = httpx.AsyncClient(transport=httpx.MockTransport(slow))
        clients.append(c)
        return c

    app.dependency_overrides[get_client_factory] = lambda: factory
    app.dependency_overrides[get_url_allowed] = lambda: (lambda url: True)  # allow-list tested separately
    with TestClient(app) as client:
        start = time.monotonic()
        r = client.get("/api/images", params={"url": f"{HOST}/slow.png"})
        assert r.status_code == 504 and "deadline" in r.json()["detail"]
        assert time.monotonic() - start < 2
    assert clients and all(c.is_closed for c in clients)
    assert ImageCache(s.image_cache_dir).get(f"{HOST}/slow.png") is None  # nothing cached on timeout


def test_the_image_endpoint_still_serves_a_prompt_cache_miss(tmp_path):
    s = make_settings(image_cache_dir=tmp_path / "cache")
    app = create_app(s)
    app.dependency_overrides[get_client_factory] = lambda: (lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, headers={"content-type": "image/png"}, content=PNG))))
    app.dependency_overrides[get_url_allowed] = lambda: (lambda url: True)  # allow-list tested separately
    with TestClient(app) as client:
        r = client.get("/api/images", params={"url": f"{HOST}/ok.png"})
        assert r.status_code == 200 and r.content == PNG and r.headers["x-image-cache"] == "miss"
        assert client.get("/api/images", params={"url": f"{HOST}/ok.png"}).headers["x-image-cache"] == "hit"
