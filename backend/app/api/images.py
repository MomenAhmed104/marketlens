"""Image proxy for the dashboard: GET /api/images?url=<image URL>.

The dashboard never hotlinks retailer images. It asks this endpoint, which serves a cached copy
(data/image_cache, git-ignored, size-bounded) or fetches the image once over plain HTTP.

Limits, so the endpoint is not a general-purpose fetcher:
  - only image URLs the backend itself found are fetched (`known_image_urls`: the catalogue
    sidecar and the images of runs still in memory), unless IMAGE_PROXY_KNOWN_URLS_ONLY=false;
  - only public http(s) addresses on ports 80/443, re-checked at connect time
    (services/images/net.py);
  - at most IMAGE_CONCURRENCY cache misses fetch at once, each within the lookup deadline, and
    requests to one host are paced;
  - only raster images whose bytes match their signature are served, with the sniffed type,
    nosniff and a no-script CSP. Failures return an error status; the dashboard shows its
    placeholder.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from app.schemas.images import ProductImage
from app.services.images import ImageCache, fetch_image
from app.services.images.net import HostPacer, UnsafeURL, check_url_syntax, safe_client

router = APIRouter(prefix="/api", tags=["images"])

_HEADERS = {
    "Cache-Control": "public, max-age=86400",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}


def _image_urls(img: ProductImage | None) -> list[str]:
    return [img.url] if img is not None and img.usable and img.url else []


def known_image_urls(app) -> set[str]:
    """Image URLs the backend found and validated: catalogue images and in-memory run listings."""
    urls: set[str] = set()
    catalogue = getattr(app.state, "catalogue", None)
    for p in (catalogue.products.values() if catalogue else ()):
        urls.update(_image_urls(p.image))
    manager = getattr(app.state, "run_manager", None)
    store = getattr(manager, "store", None)
    if store is not None:
        for run in store.list_runs():
            result = store.get_result(run.run_id)
            for pr in (result.products if result else ()):
                for c in (pr.intelligence.candidates if pr.intelligence else ()):
                    urls.update(_image_urls(c.image))
    return urls


def get_url_allowed(request: Request) -> Callable[[str], bool]:
    """Whether a cache miss may be fetched. Overridable in tests."""
    if not request.app.state.settings.image_proxy_known_urls_only:
        return lambda url: True
    return lambda url: url in known_image_urls(request.app)


def get_client_factory(request: Request) -> Callable[[], httpx.AsyncClient]:
    """How cache misses get an HTTP client. Overridable in tests (no network)."""
    timeout = request.app.state.settings.image_fetch_timeout_seconds
    return lambda: safe_client(timeout)


def _shared(request: Request) -> tuple[asyncio.Semaphore, HostPacer]:
    """Per-app limits shared by every request: outbound concurrency and per-host pacing."""
    st, s = request.app.state, request.app.state.settings
    if getattr(st, "image_proxy_limits", None) is None:
        st.image_proxy_limits = (asyncio.Semaphore(s.image_concurrency),
                                 HostPacer(s.image_per_host_concurrency, s.image_host_min_interval_seconds))  # fmt: skip
    return st.image_proxy_limits


@router.get("/images", response_class=Response, responses={200: {"content": {"image/*": {}}}})
async def get_image(
    request: Request,
    url: Annotated[str, Query(min_length=8, max_length=2048)],
    client_factory: Annotated[Callable[[], httpx.AsyncClient], Depends(get_client_factory)],
    url_allowed: Annotated[Callable[[str], bool], Depends(get_url_allowed)],
) -> Response:
    s = request.app.state.settings
    try:
        check_url_syntax(url)
    except UnsafeURL as e:
        raise HTTPException(400, detail=f"Image URL not allowed: {e}") from None
    cache = ImageCache(s.image_cache_dir, s.image_cache_max_bytes)
    hit = cache.get(url)
    if hit is not None:
        data, ctype = hit
        return Response(content=data, media_type=ctype, headers={**_HEADERS, "X-Image-Cache": "hit"})
    if not url_allowed(url):
        raise HTTPException(404, detail="Not a known product image: only images found by MarketLens are fetched")
    sem, pacer = _shared(request)
    try:
        # Same total deadline as a lookup, including the wait for a free slot; the client is
        # closed whether it finishes or expires.
        async with asyncio.timeout(s.image_lookup_deadline_seconds):
            async with sem, client_factory() as client:
                data, ctype = await fetch_image(client, url, s.image_max_bytes, pacer=pacer)
    except TimeoutError:
        raise HTTPException(504, detail="Image fetch exceeded its deadline") from None
    except UnsafeURL as e:
        raise HTTPException(400, detail=f"Image URL not allowed: {e}") from None
    except (ValueError, httpx.HTTPError) as e:
        raise HTTPException(502, detail=f"Image unavailable ({type(e).__name__})") from None
    cache.put(url, data, ctype)
    return Response(content=data, media_type=ctype, headers={**_HEADERS, "X-Image-Cache": "miss"})
