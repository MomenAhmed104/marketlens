# Product images (MarketLens)

Every catalogue product and every competitor listing can show its own image, or a consistent
placeholder. The capability is industry-neutral: nothing in it knows about musical instruments.

## Principles

- **Display only.** Images are never used by matching, price validation, availability or
  benchmark rules, and never establish a product match. A test runs matching with and without
  images and checks the results are identical.
- **Independent images.** A catalogue product's image comes from its own page; a competitor
  listing's image comes from that listing's page. The catalogue image is never reused for a
  competitor listing.
- **Traceable, never invented.** A record keeps the image URL exactly as found on the page, the
  page it came from, how it was found, when, and a status. Relative URLs are resolved against
  the page URL; nothing is guessed.
- **Failure-isolated.** Any image problem becomes a status on the record; it never fails a
  product search, extraction, match or run.
- **Free.** Plain HTTP only, no paid image API. The search provider (Exa) is not used: its
  retrieval returns page text, which has no structured data or meta tags.

## Data

`ProductImage` (`app/schemas/images.py`): `status`, `url`, `source_page_url`, `source`,
`additional_urls`, `retrieved_at`, `note`.

| Status | Meaning |
|---|---|
| `FOUND` | An image URL was extracted and served a real raster image |
| `NOT_FOUND` | The page was read but had no product image |
| `INVALID` | An image URL was found but did not serve a usable image (URL kept for traceability) |
| `BLOCKED` | The site refused automated access (401/403/429 or a bot check) |
| `FETCH_FAILED` | Network error, timeout, non-HTML or disallowed page |
| `NOT_ATTEMPTED` | Images switched off, or the listing's page was not retrieved |

- **Competitor listings:** `CompetitorProduct.image` (Phase 5 record; optional, so older saved
  runs load unchanged).
- **Catalogue products:** `StompboxProduct.image`, merged at startup from a sidecar file,
  `data/catalogue/product_images.json` (`CATALOGUE_IMAGES_PATH`). The workbook is never modified.
  A missing, unreadable or partly invalid sidecar never stops startup.

## Extraction (`app/services/images/extract.py`, pure)

Priority, first usable candidate wins:

1. Schema.org `Product` / `ProductGroup` `image` in JSON-LD (including `@graph`, lists,
   `ImageObject`, variants);
2. Open Graph `og:image:secure_url` / `og:image`;
3. product image metadata: `itemprop="image"`, `twitter:image`, `<link rel="image_src">`;
4. a product-gallery `<img>` identified by its class, id or alt text.

Logos, icons, sprites, banners, placeholders, payment and social badges, `data:` URIs, SVG,
ICO and GIF files are skipped. Further JSON-LD and gallery images become `additional_urls`.

- **Lazy-loaded and responsive images:** each `<img>` offers its alternatives in order, and the
  first usable one is kept: zoom / full-size attributes (`data-zoom-image`, `data-large_image`,
  `data-full`, `data-original`, ...), the largest `srcset` / `data-srcset` candidate (widths
  outrank densities), lazy-loading attributes (`data-src`, `data-lazy-src`, `data-lazy`, ...),
  then `src`. A placeholder `src` (a `data:` URI, `blank.gif`, a spacer) therefore never hides the
  real image. One `<img>` contributes one URL, and these alternatives stay at the `<img>`'s own
  priority level: they never outrank structured data or Open Graph.
- **`<base href>`:** relative references resolve against the first `<base href>` (itself resolved
  against the page) when it is http(s); otherwise against the page URL.

## Fetching and validation (`app/services/images/__init__.py`, `net.py`)

- `ImageFinder.find(page_url)` reads the page HTML (cap `IMAGE_PAGE_MAX_BYTES`), extracts the
  primary image, then fetches it to check it really is a raster image (JPEG, PNG, WebP, GIF,
  AVIF; cap `IMAGE_MAX_BYTES`). If the primary image fails, the listing's other images are tried.
  The validated bytes go into the cache, so the dashboard does not fetch them again.
- Only public `http(s)` addresses are fetched, on ports 80/443 and without `user:password@`. "Public"
  means globally routable unicast (`net.is_public_ip`): loopback, private, link-local, carrier-grade
  NAT (`100.64.0.0/10`), documentation, reserved, multicast and IPv4-mapped forms of these are
  refused, including after every redirect (at most 4).
- **Connect-time pinning (DNS rebinding):** every real client is `net.safe_client`, whose transport
  uses `PublicOnlyBackend`: at connect time it resolves the host again, refuses unless every address
  is public, and connects to the address it validated. A DNS answer that changes between the URL
  check and the connection cannot reach a private address. TLS still verifies the certificate against
  the host name. Environment proxies are ignored (`trust_env=False`).
- **Byte signatures:** an image is accepted only when its bytes are a JPEG, PNG, GIF, WebP or AVIF
  file (`net.sniff_image_type`), whatever the `Content-Type` says; the sniffed type is the one stored
  and served. HTML or SVG labelled as an image is refused. A declared non-image type is refused even
  if the bytes look fine; `application/octet-stream` or no type is accepted only with valid bytes.
- **Pacing:** `net.HostPacer` allows `IMAGE_PER_HOST_CONCURRENCY` (default 2) requests in flight per
  host and `IMAGE_HOST_MIN_INTERVAL_SECONDS` (default 0.5) between request starts to one host, on top
  of `IMAGE_CONCURRENCY` lookups overall. One finder (a run, a catalogue collection) shares one pacer.
- **Shared site-wide images:** generic sharing-picture file names (`default`, `fallback`, `og-image`,
  `opengraph`, `share-image`, `social-image`, `site-image`, plus the existing logo/banner/placeholder
  list) are skipped during extraction. After lookup, one page-level image (Open Graph or meta tag)
  found on several different pages of one site is marked `INVALID` ("probably a site-wide picture")
  for those listings (`flag_shared_images`; per product in a run, across products in the catalogue).
  Structured-data and gallery images are not affected; the URL is kept for traceability.
- **Total deadline:** one lookup (page, every redirect, every image candidate and its
  verification) has a single deadline, `IMAGE_LOOKUP_DEADLINE_SECONDS` (default 20). It does not
  reset per read or redirect; `IMAGE_FETCH_TIMEOUT_SECONDS` only limits each connect/read. On
  expiry the in-flight request is closed and the image is `FETCH_FAILED` ("exceeded its ... s
  deadline"); the product run continues. A `/api/images` cache miss has the same deadline (504).
- In a run, the product-intelligence agent looks up an image for every listing whose page was
  retrieved, alongside LLM 2 extraction (`IMAGE_CONCURRENCY` at a time). Settings:
  `PRODUCT_IMAGES_ENABLED` (default true), `IMAGE_FETCH_TIMEOUT_SECONDS`, `IMAGE_CONCURRENCY`.
  Tests switch images off unless they inject a fake finder, and make no network calls.

## Catalogue collection

```
cd backend
python -m app.services.images.catalogue --ids SB-001 SB-002 [--refresh] [--dry-run]
python -m app.services.images.catalogue --all [--refresh] [--dry-run]   # every product: explicit only
```

Reads each product's own `product_url` and updates the sidecar. Rules:

- `--ids` or `--all` is required, so a whole-catalogue collection never happens by accident.
  `--dry-run` lists the pages that would be read and makes no request.
- Unknown product IDs stop the command (exit 2) before any request.
- Requests to one site are paced (see Pacing). A transient failure (network error, timeout,
  HTTP 5xx, HTTP 429, deadline) is retried once after a 5 s pause; 4xx pages and disallowed URLs
  are not retried.
- Products that already have a usable image are skipped unless `--refresh`, and a refresh that
  fails never replaces a previously found image ("kept previous image").
- Records for product IDs no longer in the catalogue are dropped and reported.
- One page-level image shared by several catalogue products is marked `INVALID`.
- The sidecar is re-read just before writing; only this run's changed records are merged onto it
  (a concurrent collection for other products is kept) and it is replaced atomically through a
  unique temporary file in the same folder.

Restart the backend to load a new sidecar.

**Current sidecar (22 Nov 2026 sample of 5 products):** 4 images found (SB-017, SB-028, SB-030,
SB-090; checked visually) and 1 failure (SB-001: its catalogue `product_url` returns HTTP 404).
The other 95 products have no record and show a placeholder. A controlled collection for them has
not been run.

**Tracking:** the sidecar is currently untracked (not git-ignored). It holds only metadata about
the client's own product pages (URLs, statuses, timestamps), no third-party content, and the
backend reads it at startup, so it fits the tracked catalogue workbook next to it. Recommendation:
track it (commit it with this stage). The image cache stays git-ignored.

## Dashboard

- The browser never loads retailer images directly: `GET /api/images?url=` serves a cached copy
  (`data/image_cache/`, git-ignored, safe to delete) or fetches it once with the same safety
  rules. Responses carry `nosniff`, a no-script CSP and a one-day cache header.
- **Allow-list:** a cache miss is fetched only for an image URL MarketLens itself found and
  validated: a `FOUND` catalogue image or a `FOUND` listing image of a run still in memory
  (`known_image_urls`); anything else is 404 without any request. `IMAGE_PROXY_KNOWN_URLS_ONLY=false`
  turns this off. URL shape (scheme, port, credentials, IP literal) is checked before the cache
  (400).
- **Bounded:** at most `IMAGE_CONCURRENCY` cache misses fetch at once (the wait counts towards the
  deadline, so a flood gets 504s, not a growing queue), and the cache is capped at
  `IMAGE_CACHE_MAX_BYTES` (default 300 MB; oldest entries removed). Cache writes are atomic, and a
  cached file is served only if its bytes are still a valid image of the recorded size.
- `components/ProductThumb.tsx`: one square box everywhere (48, 72 or 132 px,
  `object-fit: contain`, white ground) and one placeholder with a plain-English reason. Clicking
  an image opens a larger view with its product page and image source links; it never replaces
  the product page link, and Escape closes only the viewer.
- Shown in: the catalogue picker (Live Runs), the Product Intelligence header (catalogue image),
  the comparison tables (each listing's image), the evidence drawer's Source tab (listing image
  and where it came from) and the Overview price cards.

## Limitations

- Sites that block automated requests, or render images only with JavaScript, show the
  placeholder.
- Image URLs can change or expire; the cache keeps a copy, the record keeps the original URL.
- Runs saved before this feature have no images (placeholders).
- The catalogue sidecar covers 5 of 100 products (4 with an image).

## Remaining risks (not fixed in Stage 8.2)

- **Not hardened for public exposure.** The backend has no authentication or rate limiting on any
  endpoint. The image endpoint is limited by the allow-list, concurrency and cache bounds above, but
  anyone who can reach the backend can still trigger fetches of known image URLs and use every other
  API. Keep it on localhost or behind an authenticating proxy. Follow-up: authentication and
  per-client rate limits for the whole API.
- **Allow-list scope.** Image URLs come from third-party pages. A hostile page could name an image
  URL on another public host; MarketLens would fetch it (public address, image bytes only, size
  capped). Follow-up if needed: restrict image hosts to the page's site and known CDNs.
- **Decoder risk.** Image bytes are signature-checked, not decoded or re-encoded, so a malformed
  image reaches the browser's decoder (as on any website). Re-encoding would need an imaging
  dependency (for example Pillow).
- **Shared-image detection is heuristic.** It only fires when the same page-level image appears on
  two or more different pages in one run or across catalogue products; a site-wide image seen on
  only one page is still shown. Legitimate listings that share one og:image (for example new and
  used listings of one model) get a placeholder.
- **Connect-time pinning relies on an httpx/httpcore internal** (`transport._pool._network_backend`,
  httpx 0.28 / httpcore 1.0). `safe_transport` refuses to start on an unknown layout, and tests
  check the pin; an httpx upgrade must re-run them.
- **Pacing is per process.** Two backend processes (or the collector and the server) pace
  independently.
