"""Find a product page's primary image in its HTML. Pure and deterministic: no network.

Priority (the first that yields a usable candidate wins):
  1. Schema.org Product `image` in JSON-LD (including @graph and lists);
  2. Open Graph `og:image:secure_url` / `og:image`;
  3. product image metadata: itemprop="image", twitter:image, <link rel="image_src">;
  4. a product-gallery <img> identified by its markup (class/id/alt mentioning product/gallery/main).

Only URLs written on the page are returned, resolved against the page's `<base href>` (when it
has one) or the page URL; nothing is guessed. Logos, icons, sprites, banners, placeholders,
tracking pixels, data: URIs and SVGs are skipped. Industry-neutral: nothing here knows about any
product category.

Lazy-loaded and responsive images: an <img> offers several alternatives, tried in order and the
first usable one kept: zoom / large-image attributes, the largest `srcset` / `data-srcset`
candidate, lazy-loading attributes (`data-src` and common variants), then `src`. So a placeholder
`src` (a data: URI, blank.gif, a spacer) never hides the real image. These alternatives stay
within the <img>'s own priority level and can never outrank structured data or Open Graph.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from app.schemas.images import ImageSource

MAX_ADDITIONAL = 6
_NOT_PRODUCT = re.compile(
    r"(?:^|[/_.\-])(?:logo|logos|favicon|sprite|icon|icons|banner|banners|placeholder|no[-_]?image|blank|spacer|"
    r"pixel|badge|payment|flag|avatar|loader|spinner|social|share|trustpilot|klarna|paypal|"
    # Generic site-wide sharing pictures (often the only og:image on every page of a site).
    r"default|fallback|og[-_]?image|opengraph|share[-_]?image|social[-_]?image|site[-_]?image)(?:[/_.\-]|$)",
    re.IGNORECASE,
)
_BAD_EXT = re.compile(r"\.(?:svg|ico|gif)(?:$|[?#])", re.IGNORECASE)
_GALLERY_HINT = re.compile(r"product|gallery|main[-_]?image|primary|zoom|featured|hero[-_]?image", re.IGNORECASE)

# <img> attributes, best first. Zoom / full-size variants, then lazy-loading variants; `src` last.
_LARGE_ATTRS = ("data-zoom-image", "data-large_image", "data-large-image", "data-full", "data-full-src",
                "data-original")  # fmt: skip
_LAZY_ATTRS = ("data-src", "data-lazy-src", "data-lazy", "data-original-src", "data-image", "data-img")
_SRCSET_ATTRS = ("srcset", "data-srcset")
_DESCRIPTOR = re.compile(r"^(\d+(?:\.\d+)?)([wx])$", re.IGNORECASE)


def usable_image_url(raw: str | None, base_url: str) -> str | None:
    """Absolute http(s) URL for an on-page image reference, or None if it cannot be a product image."""
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw or raw.startswith(("data:", "javascript:", "blob:")):
        return None
    url = urljoin(base_url, raw)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or len(url) > 2048:
        return None
    if _BAD_EXT.search(parts.path) or _NOT_PRODUCT.search(parts.path):
        return None
    return url


def parse_srcset(value: str | None) -> list[str]:
    """Image URLs in a `srcset`, best (widest, else highest density) first. Unparseable -> []."""
    if not value or not isinstance(value, str):
        return []
    items: list[tuple[float, int, str]] = []
    pos, n = 0, len(value)
    while pos < n:
        while pos < n and (value[pos].isspace() or value[pos] == ","):
            pos += 1
        start = pos
        while pos < n and not value[pos].isspace():
            pos += 1
        url = value[start:pos]
        descriptor = ""
        if url.endswith(","):  # "a.jpg, b.jpg 2x": a candidate without a descriptor
            url = url.rstrip(",")
        else:
            d_start = pos
            while pos < n and value[pos] != ",":
                pos += 1
            descriptor = value[d_start:pos].strip()
        if not url:
            continue
        m = _DESCRIPTOR.match(descriptor.split()[0]) if descriptor else None
        # Width descriptors outrank density ones; within a kind, larger is better; ties keep order.
        score = (float(m.group(1)) * (1.0 if m.group(2).lower() == "w" else 1e-3)) if m else 0.0
        items.append((score, -len(items), url))
    return [u for _, _, u in sorted(items, reverse=True)]


def _img_alternatives(a: dict[str, str]) -> tuple[str, ...]:
    out: list[str] = []
    out += [a[k] for k in _LARGE_ATTRS if a.get(k)]
    for k in _SRCSET_ATTRS:
        out += parse_srcset(a.get(k))
    out += [a[k] for k in _LAZY_ATTRS if a.get(k)]
    if a.get("src"):
        out.append(a["src"])
    return tuple(out)


@dataclass
class Candidates:
    """Raw candidates per priority level. Each entry is one element's alternatives, best first."""

    json_ld: list[tuple[str, ...]] = field(default_factory=list)
    open_graph: list[tuple[str, ...]] = field(default_factory=list)
    product_meta: list[tuple[str, ...]] = field(default_factory=list)
    page_images: list[tuple[str, ...]] = field(default_factory=list)
    base_href: str | None = None  # the first <base href>, as written


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.c = Candidates()
        self._in_ld = False
        self._ld_buf: list[str] = []
        self.ld_blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "base" and self.c.base_href is None and a.get("href"):
            self.c.base_href = a["href"]
        elif tag == "script" and a.get("type", "").lower().strip() == "application/ld+json":
            self._in_ld, self._ld_buf = True, []
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower().strip()
            content = a.get("content", "")
            if key in ("og:image:secure_url", "og:image", "og:image:url"):
                self.c.open_graph.append((content,))
            elif key in ("twitter:image", "twitter:image:src"):
                self.c.product_meta.append((content,))
            elif a.get("itemprop", "").lower() == "image":
                self.c.product_meta.append((content,))
        elif tag == "link":
            rel = a.get("rel", "").lower()
            if "image_src" in rel.split() or a.get("itemprop", "").lower() == "image":
                self.c.product_meta.append((a.get("href", ""),))
        elif tag == "img":
            alternatives = _img_alternatives(a)
            if not alternatives:
                return
            if a.get("itemprop", "").lower() == "image":
                self.c.product_meta.append(alternatives)
            elif _GALLERY_HINT.search(" ".join((a.get("class", ""), a.get("id", ""), a.get("alt", "")))):
                self.c.page_images.append(alternatives)

    def handle_data(self, data: str) -> None:
        if self._in_ld:
            self._ld_buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_ld:
            self.ld_blocks.append("".join(self._ld_buf))
            self._in_ld = False


def _is_product(node: dict) -> bool:
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.split("/")[-1] in ("Product", "ProductGroup", "IndividualProduct")
               for x in types)  # fmt: skip


def _image_values(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        v = value.get("url") or value.get("contentUrl") or value.get("@id")
        return [v] if isinstance(v, str) else []
    if isinstance(value, list):
        return [u for item in value for u in _image_values(item)]
    return []


def _walk_products(node, out: list[str], depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(node, list):
        for item in node:
            _walk_products(item, out, depth + 1)
    elif isinstance(node, dict):
        if _is_product(node):
            out.extend(_image_values(node.get("image")))
            for variant in node.get("hasVariant") or []:
                if isinstance(variant, dict):
                    out.extend(_image_values(variant.get("image")))
        for key in ("@graph", "mainEntity", "itemListElement", "item"):
            if key in node:
                _walk_products(node[key], out, depth + 1)


def collect_candidates(html: str) -> Candidates:
    p = _Collector()
    try:
        p.feed(html)
        p.close()
    except Exception:  # malformed HTML: keep whatever was collected
        pass
    for block in p.ld_blocks:
        try:
            data = json.loads(block.strip())
        except (ValueError, TypeError):
            continue
        urls: list[str] = []
        _walk_products(data, urls)
        p.c.json_ld.extend((u,) for u in urls)
    return p.c


def resolution_base(page_url: str, base_href: str | None) -> str:
    """The URL relative references resolve against: the page's <base href> when it is http(s), else the page."""
    if base_href:
        base = urljoin(page_url, base_href.strip())
        if urlsplit(base).scheme in ("http", "https") and urlsplit(base).hostname:
            return base
    return page_url


@dataclass(frozen=True)
class FoundImage:
    url: str
    source: ImageSource
    additional: list[str]


def find_primary_image(html: str, page_url: str) -> FoundImage | None:
    """The page's primary product image by the priority above, plus other images of the listing."""
    c = collect_candidates(html)
    base = resolution_base(page_url, c.base_href)
    ordered = [(ImageSource.JSON_LD, c.json_ld), (ImageSource.OPEN_GRAPH, c.open_graph),
               (ImageSource.PRODUCT_META, c.product_meta), (ImageSource.PAGE_IMAGE, c.page_images)]  # fmt: skip
    primary: tuple[str, ImageSource] | None = None
    extra: list[str] = []
    for source, entries in ordered:
        for alternatives in entries:
            # One element -> its first usable alternative (never several sizes of the same image).
            url = next((u for u in (usable_image_url(r, base) for r in alternatives) if u), None)
            if url is None:
                continue
            if primary is None:
                primary = (url, source)
            elif url != primary[0] and url not in extra and source in (ImageSource.JSON_LD, ImageSource.PAGE_IMAGE):
                extra.append(url)  # other product-gallery images of the same listing
    if primary is None:
        return None
    return FoundImage(url=primary[0], source=primary[1], additional=extra[:MAX_ADDITIONAL])
