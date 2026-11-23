"""Product images: find a listing's primary image and cache images for the dashboard.

`ImageFinder.find(page_url)` reads the product page's HTML over plain HTTP (no paid service),
extracts the primary image (`extract.find_primary_image`) and checks that the URL really serves
an image (by its byte signature, not just its Content-Type). It never raises: every failure
becomes a `ProductImage` with a status and a note, so a missing image can never break product
search, extraction or matching. Images are display-only and are never used to decide a match.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from app.schemas.images import ImageSource, ImageStatus, ProductImage
from app.services.images.extract import find_primary_image
from app.services.images.net import HostPacer, UnsafeURL, safe_client, safe_get, sniff_image_type

# Raster types the dashboard may show. SVG is excluded (it can carry script).
IMAGE_TYPES = frozenset({"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif", "image/avif"})
# Declared types that are accepted when the bytes themselves are a recognised raster image
# (some CDNs send no specific type). The served type is always the sniffed one.
_GENERIC_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})
_BLOCK_STATUS = {401, 403, 429}
_CHALLENGE = ("just a moment", "client challenge", "access denied", "attention required", "are you a robot")
# Sources that are often one site-wide picture rather than this product's (shared-image check).
_SHAREABLE_SOURCES = frozenset({ImageSource.OPEN_GRAPH, ImageSource.PRODUCT_META})


def _now() -> datetime:
    return datetime.now(UTC)


class ImageLookup(Protocol):
    async def find(self, page_url: str) -> ProductImage: ...

    async def aclose(self) -> None: ...


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via a unique temporary file in the same directory, then replace (never a partial file)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class ImageCache:
    """Disk cache of validated image bytes, keyed by the image URL. Git-ignored; safe to delete.

    Writes are atomic (unique temporary file, then replace), so concurrent requests for the same
    image never see a partial file. The total size is bounded (`max_bytes`): after a write the
    least recently written entries are removed until the cache is back under the bound."""

    def __init__(self, directory: Path, max_bytes: int | None = None):
        self.dir = Path(directory)
        self.max_bytes = max_bytes

    def _paths(self, url: str) -> tuple[Path, Path]:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.dir / f"{key}.bin", self.dir / f"{key}.json"

    def get(self, url: str) -> tuple[bytes, str] | None:
        body, meta = self._paths(url)
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            if info.get("url") != url:
                return None
            data = body.read_bytes()
        except (OSError, ValueError, KeyError):
            return None
        ctype = sniff_image_type(data)  # never serve bytes that are not a recognised raster image
        if ctype is None or ("bytes" in info and info["bytes"] != len(data)):
            return None
        return data, ctype

    def put(self, url: str, data: bytes, content_type: str) -> None:
        body, meta = self._paths(url)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            _atomic_write(body, data)  # body first: the metadata file marks a complete entry
            _atomic_write(meta, json.dumps({"url": url, "content_type": content_type, "bytes": len(data),
                                            "cached_at": _now().isoformat()}).encode("utf-8"))  # fmt: skip
        except OSError:
            return  # a cache that cannot be written only costs a refetch
        if self.max_bytes is not None:
            self.enforce_limit(keep=meta)

    def enforce_limit(self, keep: Path | None = None) -> int:
        """Remove the oldest entries until the cache is within `max_bytes`. Returns entries removed."""
        if self.max_bytes is None:
            return 0
        entries: list[tuple[float, int, Path, Path]] = []
        total = 0
        try:
            for m in self.dir.glob("*.json"):
                b = m.with_suffix(".bin")
                try:
                    size = b.stat().st_size + m.stat().st_size
                    entries.append((m.stat().st_mtime, size, m, b))
                except OSError:
                    continue
                total += size
        except OSError:
            return 0
        removed = 0
        for _, size, m, b in sorted(entries, key=lambda e: e[0]):
            if total <= self.max_bytes:
                break
            if keep is not None and m == keep:
                continue
            for p in (m, b):
                try:
                    p.unlink()
                except OSError:
                    pass
            total -= size
            removed += 1
        return removed


async def fetch_image(client: httpx.AsyncClient, url: str, max_bytes: int,
                      pacer: HostPacer | None = None) -> tuple[bytes, str]:  # fmt: skip
    """The image's bytes and real content type, or ValueError/UnsafeURL/httpx error if it is not usable."""
    got = await safe_get(client, url, max_bytes, accept="image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
                         pacer=pacer)  # fmt: skip
    if got.status != 200:
        raise ValueError(f"image request returned HTTP {got.status}")
    if got.content_type not in IMAGE_TYPES and got.content_type not in _GENERIC_TYPES:
        raise ValueError(f"not a supported image type ({got.content_type or 'none'})")
    if got.truncated:
        raise ValueError(f"image larger than {max_bytes} bytes")
    if len(got.body) < 64:
        raise ValueError("image is empty")
    sniffed = sniff_image_type(got.body)
    if sniffed is None:
        raise ValueError(f"content is not a recognised raster image (declared {got.content_type or 'none'})")
    return got.body, sniffed


class ImageFinder:
    def __init__(self, settings, cache: ImageCache | None = None, client: httpx.AsyncClient | None = None,
                 pacer: HostPacer | None = None):  # fmt: skip
        self.s = settings
        self.cache = cache
        self.client = client or safe_client(settings.image_fetch_timeout_seconds)
        self.pacer = pacer or HostPacer(settings.image_per_host_concurrency, settings.image_host_min_interval_seconds)
        self._sem = asyncio.Semaphore(settings.image_concurrency)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def find(self, page_url: str) -> ProductImage:
        async with self._sem:
            # One total deadline for the whole lookup (page, every redirect, every image candidate
            # and its verification). It starts once this lookup may run, so queueing behind other
            # lookups does not count. On expiry the in-flight request's stream is closed by its
            # context manager and the lookup ends as FETCH_FAILED; the product run is unaffected.
            deadline = self.s.image_lookup_deadline_seconds
            try:
                async with asyncio.timeout(deadline):
                    return await self._find(page_url)
            except TimeoutError:
                return ProductImage(status=ImageStatus.FETCH_FAILED, source_page_url=page_url, retrieved_at=_now(),
                                    note=f"Image lookup exceeded its {deadline:g} s deadline")  # fmt: skip
            except Exception as e:  # never let an image break the product flow
                return ProductImage(status=ImageStatus.FETCH_FAILED, source_page_url=page_url, retrieved_at=_now(),
                                    note=f"Image lookup failed ({type(e).__name__})")  # fmt: skip

    async def _find(self, page_url: str) -> ProductImage:
        base = dict(source_page_url=page_url, retrieved_at=_now())
        try:
            page = await safe_get(self.client, page_url, self.s.image_page_max_bytes,
                                  accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.5", pacer=self.pacer)  # fmt: skip
        except UnsafeURL as e:
            return ProductImage(status=ImageStatus.FETCH_FAILED, note=f"Page URL not allowed: {e}", **base)
        except httpx.HTTPError as e:
            return ProductImage(status=ImageStatus.FETCH_FAILED, note=f"Page could not be fetched ({type(e).__name__})",
                                **base)  # fmt: skip
        base["source_page_url"] = page.url
        if page.status in _BLOCK_STATUS:
            return ProductImage(status=ImageStatus.BLOCKED, note=f"The site refused automated access (HTTP {page.status})",
                                **base)  # fmt: skip
        if page.status != 200:
            return ProductImage(status=ImageStatus.FETCH_FAILED, note=f"Page returned HTTP {page.status}", **base)
        if "html" not in page.content_type:
            return ProductImage(status=ImageStatus.FETCH_FAILED, note=f"Page is not HTML ({page.content_type or 'none'})",
                                **base)  # fmt: skip
        html = page.body.decode("utf-8", errors="replace")
        found = find_primary_image(html, page.url)
        if found is None:
            head = html[:4000].lower()
            if len(html) < 20_000 and any(w in head for w in _CHALLENGE):
                return ProductImage(status=ImageStatus.BLOCKED, note="The site answered with a bot check", **base)
            return ProductImage(status=ImageStatus.NOT_FOUND, note="No product image found in the page markup", **base)
        # Validate: the primary must really serve an image; fall back to the listing's other images.
        problems = []
        for url in [found.url, *found.additional]:
            try:
                data, ctype = await fetch_image(self.client, url, self.s.image_max_bytes, pacer=self.pacer)
            except (ValueError, UnsafeURL, httpx.HTTPError) as e:
                problems.append(f"{type(e).__name__}: {e}"[:160])
                continue
            if self.cache is not None:
                self.cache.put(url, data, ctype)
            return ProductImage(status=ImageStatus.FOUND, url=url, source=found.source,
                                additional_urls=[u for u in [found.url, *found.additional] if u != url],
                                note=None if url == found.url else "The first image did not load; used the next one",
                                **base)  # fmt: skip
        return ProductImage(status=ImageStatus.INVALID, url=found.url, source=found.source,
                            additional_urls=found.additional, note="Image URL did not serve a usable image: " +
                            "; ".join(problems[:2]), **base)  # fmt: skip


def shared_image_note(count: int, scope: str) -> str:
    return (f"The same page-level image (Open Graph or meta tag) was found on {count} different {scope}; "
            "it is probably a site-wide picture, not this product's, so it is not shown")


def flag_shared_images(images: dict[str, ProductImage | None], scope: str) -> list[str]:
    """Mark FOUND images that several different pages of one site share as INVALID, in place.

    `images` maps a page key (a product ID or a listing URL) to its image. Only page-level
    sources (Open Graph, meta tags) are checked: a site that sets one og:image for every page
    would otherwise show the same picture for every product. Structured-data and gallery
    images are left alone. The URL is kept for traceability. Returns the keys that changed."""
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for key, img in images.items():
        if img is not None and img.usable and img.source in _SHAREABLE_SOURCES:
            site = (urlsplit(img.source_page_url or "").hostname or "").lower()
            groups[(site, img.url)].append(key)  # type: ignore[arg-type]
    changed: list[str] = []
    for keys in groups.values():
        pages = {(images[k].source_page_url or k) for k in keys}  # type: ignore[union-attr]
        if len(pages) < 2:
            continue
        for k in keys:
            img = images[k]
            images[k] = img.model_copy(update={"status": ImageStatus.INVALID,  # type: ignore[union-attr]
                                               "note": shared_image_note(len(pages), scope)})  # fmt: skip
            changed.append(k)
    return changed


def build_image_finder(settings) -> ImageFinder | None:
    """The real finder when product images are enabled, else None (images NOT_ATTEMPTED)."""
    if not settings.product_images_enabled:
        return None
    return ImageFinder(settings, cache=ImageCache(settings.image_cache_dir, settings.image_cache_max_bytes))


def not_attempted(page_url: str | None, why: str) -> ProductImage:
    return ProductImage(status=ImageStatus.NOT_ATTEMPTED, source_page_url=page_url, note=why)
