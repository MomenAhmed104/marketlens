"""Stage 8.2 image hardening: address checks, connect-time pinning (DNS rebinding), byte
signatures, bounded atomic cache, endpoint allow-list, per-host pacing, shared site-wide images
and the catalogue collector's rules.

No network: HTTP goes through httpx.MockTransport with public IP-literal hosts; the connect-time
tests use a local socket server that must never receive a connection, and a patched resolver.
"""

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.images import get_client_factory, known_image_urls
from app.main import create_app
from app.schemas.images import ImageSource, ImageStatus, ProductImage
from app.services.images import ImageCache, fetch_image, flag_shared_images
from app.services.images import net
from app.services.images.catalogue import (
    UnknownProductIds,
    collect,
    is_retryable,
    load_catalogue_images,
    main,
    save,
    write_catalogue_images,
)
from app.services.images.net import (
    HostPacer,
    PublicOnlyBackend,
    UnsafeURL,
    check_url_syntax,
    is_public_ip,
    safe_client,
    sniff_image_type,
)
from tests.search_fakes import make_settings
from tests.test_intelligence_agent import cands, make, run

HOST = "http://93.184.216.34"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 200
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>' + b" " * 100


# --------------------------------------------------------------------------- addresses and URLs


@pytest.mark.parametrize("ip, public", [
    ("8.8.8.8", True), ("93.184.216.34", True), ("2606:4700::1111", True),
    ("100.64.0.1", False), ("100.127.255.254", False),  # carrier-grade NAT
    ("127.0.0.1", False), ("10.1.2.3", False), ("172.16.0.1", False), ("192.168.1.1", False),
    ("169.254.169.254", False), ("0.0.0.0", False), ("224.0.0.1", False), ("192.0.2.10", False),
    ("::1", False), ("fe80::1", False), ("fd00::1", False), ("::ffff:127.0.0.1", False), ("::ffff:10.0.0.1", False),
    ("not-an-ip", False),
])  # fmt: skip
def test_only_globally_routable_unicast_addresses_are_public(ip, public):
    assert is_public_ip(ip) is public


@pytest.mark.parametrize("url", [
    "ftp://93.184.216.34/a.png", "javascript:alert(1)", "data:image/png;base64,AAAA", "file:///etc/passwd",
    "http://user:pw@93.184.216.34/a.png", "http://93.184.216.34:8080/a.png", "http://93.184.216.34:22/a.png",
    "http://100.64.0.1/a.png", "http://[::ffff:127.0.0.1]/a.png", "http:///a.png", "http://93.184.216.34:99999/a.png",
])  # fmt: skip
def test_disallowed_url_shapes_are_refused_without_dns(url):
    with pytest.raises(UnsafeURL):
        check_url_syntax(url)


def test_standard_ports_and_hostnames_pass_the_syntax_check():
    assert check_url_syntax("https://cdn.example.com/a.png") == "cdn.example.com"
    assert check_url_syntax("http://93.184.216.34:80/a.png") == "93.184.216.34"
    assert check_url_syntax("https://93.184.216.34:443/a.png") == "93.184.216.34"


# --------------------------------------------------------------------------- connect-time pinning


def test_the_transport_refuses_a_private_address_even_when_the_url_check_is_bypassed(monkeypatch):
    """Simulates DNS rebinding: the pre-request check passes, the connection must still be refused."""
    hits = []

    async def scenario():
        server = await asyncio.start_server(lambda r, w: hits.append(1) or w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            async def passes(url):
                return None

            monkeypatch.setattr(net, "check_public_url", passes)
            async with safe_client(2) as client:
                with pytest.raises(UnsafeURL):
                    await net.safe_get(client, f"http://127.0.0.1:{port}/a.png", 1000, accept="*/*")
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
    assert hits == []  # nothing ever connected to the local server


def test_a_dns_answer_that_changes_after_the_check_is_caught_at_connect_time(monkeypatch):
    answers = iter([[(2, 1, 6, "", ("93.184.216.34", 80))], [(2, 1, 6, "", ("127.0.0.1", 80))]])

    async def rebinding(host, port):
        return next(answers)

    monkeypatch.setattr(net, "_getaddrinfo", rebinding)

    async def scenario():
        await net.check_public_url("http://rebind.example/a.png")  # first answer: public, passes
        with pytest.raises(UnsafeURL):
            await PublicOnlyBackend().connect_tcp("rebind.example", 80)  # second answer: private

    asyncio.run(scenario())


def test_the_backend_connects_to_the_address_it_validated(monkeypatch):
    seen = []

    class Inner:
        async def connect_tcp(self, host, port, **kw):
            seen.append((host, port))
            return "stream"

        async def sleep(self, s):
            pass

    async def resolve(host, port):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(net, "_getaddrinfo", resolve)
    assert asyncio.run(PublicOnlyBackend(Inner()).connect_tcp("shop.example", 443)) == "stream"
    assert seen == [("93.184.216.34", 443)]  # the validated IP, never a second lookup by the socket layer
    with pytest.raises(UnsafeURL):
        asyncio.run(PublicOnlyBackend(Inner()).connect_unix_socket("/var/run/docker.sock"))


def test_real_clients_ignore_environment_proxies():
    c = safe_client(5)
    assert c._trust_env is False and isinstance(c._transport._pool._network_backend, PublicOnlyBackend)
    asyncio.run(c.aclose())


# --------------------------------------------------------------------------- byte signatures


def test_image_signatures():
    assert sniff_image_type(PNG) == "image/png" and sniff_image_type(JPEG) == "image/jpeg"
    assert sniff_image_type(WEBP) == "image/webp" and sniff_image_type(b"GIF89a" + b"\x00" * 80) == "image/gif"
    assert sniff_image_type(b"\x00\x00\x00\x1cftypavif" + b"\x00" * 80) == "image/avif"
    assert sniff_image_type(SVG) is None and sniff_image_type(b"<html>" + b" " * 100) is None


def mock(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("declared, body, expected", [
    ("image/png", PNG, "image/png"),
    ("image/jpeg", PNG, "image/png"),  # the served type is the real one, not the header
    ("application/octet-stream", JPEG, "image/jpeg"),
    ("", WEBP, "image/webp"),
])  # fmt: skip
def test_fetch_image_trusts_the_bytes_not_the_header(declared, body, expected):
    client = mock(lambda r: httpx.Response(200, headers={"content-type": declared}, content=body))
    assert asyncio.run(fetch_image(client, f"{HOST}/a", 10_000)) == (body, expected)


@pytest.mark.parametrize("declared, body", [
    ("image/png", b"<html><script>alert(1)</script></html>" + b" " * 100),  # HTML labelled as an image
    ("image/png", SVG),
    ("image/svg+xml", SVG),
    ("text/html", PNG),  # a declared non-image type is refused even if the bytes look fine
])  # fmt: skip
def test_fetch_image_refuses_mislabelled_or_active_content(declared, body):
    client = mock(lambda r: httpx.Response(200, headers={"content-type": declared}, content=body))
    with pytest.raises(ValueError):
        asyncio.run(fetch_image(client, f"{HOST}/a", 10_000))


# --------------------------------------------------------------------------- cache


def test_cache_writes_are_atomic_and_leave_no_temporary_files(tmp_path):
    cache = ImageCache(tmp_path)
    cache.put(f"{HOST}/a.png", PNG, "image/png")
    assert cache.get(f"{HOST}/a.png") == (PNG, "image/png")
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_cache_never_serves_bytes_that_are_not_an_image(tmp_path):
    cache = ImageCache(tmp_path)
    cache.put(f"{HOST}/a.png", PNG, "image/png")
    body, _ = cache._paths(f"{HOST}/a.png")
    body.write_bytes(SVG)  # tampered or left over from older code
    assert cache.get(f"{HOST}/a.png") is None
    body.write_bytes(PNG[:100])  # truncated: size no longer matches the metadata
    assert cache.get(f"{HOST}/a.png") is None


def test_cache_size_is_bounded_and_the_oldest_entries_go_first(tmp_path):
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * 400_000
    cache = ImageCache(tmp_path, max_bytes=1_000_000)
    for i in range(4):
        cache.put(f"{HOST}/{i}.png", big, "image/png")
        meta = cache._paths(f"{HOST}/{i}.png")[1]
        t = time.time() - 100 + i  # distinct, increasing write times
        import os

        os.utime(meta, (t, t))
        cache.enforce_limit()
    kept = [i for i in range(4) if cache.get(f"{HOST}/{i}.png")]
    assert kept == [2, 3]  # newest kept, total under the bound
    total = sum(p.stat().st_size for p in tmp_path.iterdir())
    assert total <= 1_000_000


# --------------------------------------------------------------------------- endpoint allow-list


def test_the_endpoint_fetches_only_images_the_backend_found(tmp_path):
    sidecar = tmp_path / "product_images.json"
    write_catalogue_images(sidecar, {"SB-001": ProductImage(status=ImageStatus.FOUND, url=f"{HOST}/known.png",
                                                            source=ImageSource.JSON_LD)})  # fmt: skip
    s = make_settings(image_cache_dir=tmp_path / "cache", catalogue_images_path=sidecar)
    app = create_app(s)
    made = []

    def factory():
        made.append(1)
        return mock(lambda r: httpx.Response(200, headers={"content-type": "image/png"}, content=PNG))

    app.dependency_overrides[get_client_factory] = lambda: factory
    with TestClient(app) as client:
        r = client.get("/api/images", params={"url": f"{HOST}/unknown.png"})
        assert r.status_code == 404 and made == []  # never fetched
        r = client.get("/api/images", params={"url": f"{HOST}/known.png"})
        assert r.status_code == 200 and r.content == PNG and r.headers["x-image-cache"] == "miss"
        assert r.headers["content-security-policy"].startswith("default-src 'none'")
        assert client.get("/api/images", params={"url": f"{HOST}:8080/known.png"}).status_code == 400
        assert client.get("/api/images", params={"url": "http://user:pw@93.184.216.34/x.png"}).status_code == 400
        assert client.get("/api/images", params={"url": "http://100.64.1.1/x.png"}).status_code == 400


def test_the_endpoint_allow_list_can_be_switched_off(tmp_path):
    s = make_settings(image_cache_dir=tmp_path / "cache", image_proxy_known_urls_only=False)
    app = create_app(s)
    app.dependency_overrides[get_client_factory] = lambda: (
        lambda: mock(lambda r: httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)))
    with TestClient(app) as client:
        assert client.get("/api/images", params={"url": f"{HOST}/any.png"}).status_code == 200


def test_known_image_urls_come_from_the_catalogue_and_runs_in_memory():
    img = lambda u, st=ImageStatus.FOUND: ProductImage(status=st, url=u)  # noqa: E731
    cand = lambda i: SimpleNamespace(image=i)  # noqa: E731
    result = SimpleNamespace(products=[SimpleNamespace(intelligence=SimpleNamespace(
        candidates=[cand(img(f"{HOST}/listing.png")), cand(img(f"{HOST}/bad.png", ImageStatus.INVALID)), cand(None)])),
        SimpleNamespace(intelligence=None)])  # fmt: skip
    store = SimpleNamespace(list_runs=lambda: [SimpleNamespace(run_id="r1")], get_result=lambda rid: result)
    app = SimpleNamespace(state=SimpleNamespace(
        catalogue=SimpleNamespace(products={"SB-1": SimpleNamespace(image=img(f"{HOST}/cat.png")),
                                            "SB-2": SimpleNamespace(image=None)}),
        run_manager=SimpleNamespace(store=store)))  # fmt: skip
    assert known_image_urls(app) == {f"{HOST}/cat.png", f"{HOST}/listing.png"}  # INVALID images are not fetched


# --------------------------------------------------------------------------- pacing


def test_requests_to_one_host_are_spaced_and_other_hosts_are_not_delayed():
    pacer = HostPacer(per_host=1, min_interval=0.2)
    starts: dict[str, list[float]] = {"a": [], "b": []}

    async def go(url, key):
        async with pacer.slot(url):
            starts[key].append(time.monotonic())

    async def scenario():
        t0 = time.monotonic()
        await asyncio.gather(go("https://a.example/1", "a"), go("https://a.example/2", "a"),
                             go("https://a.example/3", "a"), go("https://b.example/1", "b"))  # fmt: skip
        return t0

    t0 = asyncio.run(scenario())
    a = sorted(starts["a"])
    assert all(later - earlier >= 0.18 for earlier, later in zip(a, a[1:]))
    assert starts["b"][0] - t0 < 0.1


# --------------------------------------------------------------------------- shared site-wide images


def og(url, page, source=ImageSource.OPEN_GRAPH):
    return ProductImage(status=ImageStatus.FOUND, url=url, source=source, source_page_url=page)


def test_one_og_image_on_several_pages_of_a_site_is_not_a_product_image():
    images = {
        "a": og("https://shop.example/share.jpg", "https://shop.example/p/1"),
        "b": og("https://shop.example/share.jpg", "https://shop.example/p/2"),
        "c": og("https://shop.example/c.jpg", "https://shop.example/p/3"),
        "d": og("https://cdn.example/x.jpg", "https://shop.example/p/4", ImageSource.JSON_LD),
        "e": og("https://cdn.example/x.jpg", "https://shop.example/p/5", ImageSource.JSON_LD),
        "f": None,
    }
    assert sorted(flag_shared_images(images, "pages")) == ["a", "b"]
    assert images["a"].status is ImageStatus.INVALID and images["a"].url == "https://shop.example/share.jpg"
    assert "site-wide" in images["a"].note and not images["a"].usable
    assert images["c"].usable and images["d"].usable and images["e"].usable  # structured data is left alone


def test_the_same_page_listed_twice_is_not_a_shared_image():
    images = {"a": og("https://s.example/i.jpg", "https://s.example/p"), "b": og("https://s.example/i.jpg", "https://s.example/p")}
    assert flag_shared_images(images, "pages") == []


def test_listings_sharing_one_og_image_get_a_placeholder_in_a_run(catalogue):
    # The fixture retrieves one listing per real site, so the fake reports every listing as a
    # different page of one site, all with the same og:image (a site-wide sharing picture).
    class SameImage:
        async def find(self, page_url):
            page = f"https://one-site.example/{abs(hash(page_url))}"
            return og("https://one-site.example/site-card.webp", page)

        async def aclose(self):
            pass

    agent = make(catalogue)
    agent.images = SameImage()
    c = cands(run(agent, catalogue))
    looked_up = [x for x in c.values() if x.image and x.image.source is not None]  # not NOT_ATTEMPTED
    assert len(looked_up) >= 2
    assert all(x.image.status is ImageStatus.INVALID and "site-wide" in x.image.note for x in looked_up)
    # Display only: extraction is unaffected (compare with a run without images).
    baseline = cands(run(make(catalogue), catalogue))
    assert {u: (x.extraction_status, x.price) for u, x in c.items()} == {
        u: (x.extraction_status, x.price) for u, x in baseline.items()}


def test_distinct_listing_images_are_kept_in_a_run(catalogue):
    class OwnImage:
        async def find(self, page_url):
            return og(f"{page_url}/own.webp", page_url)

        async def aclose(self):
            pass

    agent = make(catalogue)
    agent.images = OwnImage()
    looked_up = [x for x in cands(run(agent, catalogue)).values() if x.image and x.image.source is not None]
    assert looked_up and all(x.image.usable for x in looked_up)


def test_generic_sharing_picture_filenames_are_skipped():
    from app.services.images.extract import usable_image_url

    for name in ("default.jpg", "og-image.png", "og_image.jpg", "opengraph.png", "share-image.jpg", "fallback.webp"):
        assert usable_image_url(f"/assets/{name}", "https://shop.example/p") is None, name
    assert usable_image_url("/media/boss-rc-5-front.jpg", "https://shop.example/p")


# --------------------------------------------------------------------------- catalogue collector


class Finder:
    def __init__(self, results):
        self.results, self.asked = results, []

    async def find(self, page_url):
        self.asked.append(page_url)
        r = self.results(page_url, self.asked.count(page_url))
        return r

    async def aclose(self):
        pass


def settings_with(tmp_path, images=None):
    s = make_settings(catalogue_images_path=tmp_path / "product_images.json", image_cache_dir=tmp_path / "cache")
    if images is not None:
        write_catalogue_images(s.catalogue_images_path, images)
    return s


def found(page, url=None):
    return ProductImage(status=ImageStatus.FOUND, url=url or f"{page}/img.jpg", source=ImageSource.JSON_LD,
                        source_page_url=page)  # fmt: skip


def failed(page, note):
    return ProductImage(status=ImageStatus.FETCH_FAILED, source_page_url=page, note=note)


def test_unknown_product_ids_stop_collection_before_any_request(tmp_path):
    f = Finder(lambda u, n: found(u))
    with pytest.raises(UnknownProductIds) as e:
        asyncio.run(collect(settings_with(tmp_path), ["SB-001", "SB-NOPE"], refresh=False, finder=f))
    assert e.value.ids == ["SB-NOPE"] and f.asked == []


def test_stale_records_are_dropped_and_reported(tmp_path):
    s = settings_with(tmp_path, {"SB-001": found("https://p/1"), "SB-GONE": found("https://p/gone")})
    report = asyncio.run(collect(s, ["SB-002"], refresh=False, finder=Finder(lambda u, n: found(u))))
    assert report.stale_removed == ["SB-GONE"] and "SB-GONE" not in report.images
    save(s, report)
    assert set(load_catalogue_images(s.catalogue_images_path)) == {"SB-001", "SB-002"}


def test_a_failed_refresh_never_replaces_a_found_image(tmp_path):
    s = settings_with(tmp_path, {"SB-001": found("https://p/1")})
    report = asyncio.run(collect(s, ["SB-001"], refresh=True, finder=Finder(lambda u, n: failed(u, "Page returned HTTP 404")),
                                 retry_pause=0))  # fmt: skip
    assert report.kept_previous == ["SB-001"] and report.images["SB-001"].usable and report.changed == []


def test_transient_failures_are_retried_once_and_4xx_are_not(tmp_path):
    def results(url, n):  # every product fails transiently on its first attempt only
        return failed(url, "Page could not be fetched (ConnectTimeout)") if n == 1 else found(url)

    s = settings_with(tmp_path)
    f = Finder(results)
    report = asyncio.run(collect(s, ["SB-001", "SB-002"], refresh=False, finder=f, retry_pause=0))
    assert sorted(report.retried) == ["SB-001", "SB-002"] and all(i.usable for i in report.images.values())
    assert len(f.asked) == 4  # one retry each, never more

    f404 = Finder(lambda u, n: failed(u, "Page returned HTTP 404"))
    report = asyncio.run(collect(settings_with(tmp_path / "b"), ["SB-001"], refresh=False, finder=f404, retry_pause=0))
    assert report.retried == [] and len(f404.asked) == 1


@pytest.mark.parametrize("img, retry", [
    (ProductImage(status=ImageStatus.FETCH_FAILED, note="Page returned HTTP 503"), True),
    (ProductImage(status=ImageStatus.FETCH_FAILED, note="Page returned HTTP 404"), False),
    (ProductImage(status=ImageStatus.FETCH_FAILED, note="Image lookup exceeded its 20 s deadline"), True),
    (ProductImage(status=ImageStatus.FETCH_FAILED, note="Page URL not allowed: host is not a public address"), False),
    (ProductImage(status=ImageStatus.BLOCKED, note="The site refused automated access (HTTP 429)"), True),
    (ProductImage(status=ImageStatus.BLOCKED, note="The site refused automated access (HTTP 403)"), False),
    (ProductImage(status=ImageStatus.NOT_FOUND, note="No product image found in the page markup"), False),
])  # fmt: skip
def test_retry_rules(img, retry):
    assert is_retryable(img) is retry


def test_one_og_image_shared_by_catalogue_products_is_marked_invalid(tmp_path):
    s = settings_with(tmp_path)
    share = lambda u, n: og("https://stompbox.example/share.jpg", u)  # noqa: E731
    report = asyncio.run(collect(s, ["SB-001", "SB-002"], refresh=False, finder=Finder(share)))
    assert report.shared == ["SB-001", "SB-002"] and not any(i.usable for i in report.images.values())


def test_saving_keeps_records_written_by_another_collection_meanwhile(tmp_path):
    s = settings_with(tmp_path, {"SB-001": found("https://p/1")})
    report = asyncio.run(collect(s, ["SB-002"], refresh=False, finder=Finder(lambda u, n: found(u))))
    # Another collection writes SB-003 after this one read the sidecar.
    current = load_catalogue_images(s.catalogue_images_path)
    write_catalogue_images(s.catalogue_images_path, {**current, "SB-003": found("https://p/3")})
    save(s, report)
    on_disk = load_catalogue_images(s.catalogue_images_path)
    assert set(on_disk) == {"SB-001", "SB-002", "SB-003"}
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert json.loads(s.catalogue_images_path.read_text(encoding="utf-8"))["version"] == 1


def test_the_command_needs_explicit_ids_or_all():
    with pytest.raises(SystemExit):
        main([])  # no accidental whole-catalogue collection
    with pytest.raises(SystemExit):
        main(["--ids"])


def test_the_suite_cannot_reach_the_internet():
    """conftest.no_outbound_network: DNS for a real name and non-loopback connections fail."""
    import socket

    with pytest.raises(OSError):
        socket.getaddrinfo("example.com", 443)
    s = socket.socket()
    try:
        with pytest.raises(OSError, match="network disabled"):
            s.connect(("93.184.216.34", 80))
    finally:
        s.close()
