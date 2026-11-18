"""Deterministic domain normalisation."""

import re
from urllib.parse import urlsplit


def normalize_hostname(url_or_host: str) -> str:
    """Return a canonical hostname: lowercase, no scheme/port/path, no leading 'www.'.

    Accepts a full URL ("https://www.Andertons.co.uk/x") or a bare host
    ("andertons.co.uk"). Raises ValueError if no hostname can be found.
    """
    value = url_or_host.strip()
    if "://" not in value:
        value = "//" + value
    host = urlsplit(value).hostname  # lowercases and strips port/userinfo
    if not host or "." not in host:
        raise ValueError(f"Cannot extract a hostname from {url_or_host!r}")
    host = host.rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


# Two-label public suffixes we expect to meet. Not a full public-suffix list:
# unknown multi-label suffixes fall back to the last two labels.
_TWO_LABEL_SUFFIXES = {
    "co.uk", "org.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk", "ac.uk", "gov.uk",
    "com.au", "net.au", "org.au", "co.nz", "co.jp", "com.br", "co.za", "com.cn",
}  # fmt: skip


def registrable_domain(url_or_host: str) -> str:
    """The domain a company registers, e.g. 'blog.andertons.co.uk' -> 'andertons.co.uk'.

    Used to group hostnames of the same site and to deduplicate against known
    competitors. Raises ValueError like normalize_hostname.
    """
    host = normalize_hostname(url_or_host)
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def canonical_domain(value: str) -> str:
    """The one stored key for a domain-level record (e.g. UK eligibility): its registrable domain.

    Strict, unlike `registrable_domain`: only case, surrounding whitespace, a trailing
    dot and a leading 'www.' are normalised ('WWW.Bax-Shop.co.uk' -> 'bax-shop.co.uk').
    Anything ambiguous raises ValueError instead of being guessed at: a URL, path,
    port or credentials, another subdomain ('shop.example.co.uk'), or invalid labels.
    """
    if not isinstance(value, str):
        raise ValueError("domain must be a string")
    host = value.strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
        raise ValueError(f"Not a bare domain name: {value!r} (no scheme, path, port or invalid characters)")
    if host in _TWO_LABEL_SUFFIXES:
        raise ValueError(f"{value!r} is a public suffix, not a domain")
    if registrable_domain(host) != host:
        raise ValueError(f"{value!r} is a subdomain; give the registrable domain {registrable_domain(host)!r}")
    return host
