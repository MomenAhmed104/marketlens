"""Deterministic URL canonicalisation.

The canonical form is used to deduplicate and to choose which URL to fetch. The
original URL is always kept separately as evidence of what the provider returned.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

# Gear4music serves country/language storefronts under a /<cc>/<lang>/ prefix
# (e.g. /us/en/...). The UK storefront has no prefix and shows GBP prices.
_GEAR4MUSIC_LOCALE = re.compile(r"^/[a-z]{2}/[a-z]{2}(?=/|$)", re.IGNORECASE)


def _is_host(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def canonical_url(url: str) -> str:
    """Return a canonical URL.

    - Lowercases the scheme and host and drops default ports and fragments.
    - Removes trailing slashes from the path (the root path stays "/").
    - Maps Gear4music locale storefront paths to the UK storefront.
    Query strings are kept: on some retailers they select a product variant.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        raise ValueError(f"Not an absolute URL: {url!r}")
    port = parts.port
    netloc = host if port is None or (scheme, port) in {("http", 80), ("https", 443)} else f"{host}:{port}"

    path = parts.path or "/"
    if _is_host(host, "gear4music.com"):
        path = _GEAR4MUSIC_LOCALE.sub("", path) or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))
